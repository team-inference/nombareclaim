import json
import logging

from fastapi import APIRouter, Request, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import FailureEvent, FailureStatus
from app.services.signature import verify_signature, extract_event, SignatureVerificationError
from app.services.classification import classify_failure
from app.services.recovery import confirm_recovery_if_paid, maybe_auto_recover

logger = logging.getLogger("nombareclaim.webhooks")

router = APIRouter()

# Event types that represent a failed/abandoned payment and should be
# stored as a recoverable FailureEvent, vs. a success event that might
# confirm a recovery in progress. Both are now configurable via
# NOMBA_FAILURE_EVENT_TYPES / NOMBA_SUCCESS_EVENT_TYPES (see
# config.py) rather than hardcoded here, specifically because neither
# is fully confirmed by any doc source seen so far:
#
# IMPORTANT — genuinely unresolved, flagged rather than guessed past:
# Nomba's own training material's "Common event types" table lists
# only payment_success, virtual_account.funded, transfer.success,
# transfer.failed, mandate.debit_success — it does NOT include a
# payment-failed event at all. A separate, different endpoint's
# reference (the event-log/replay API) lists PAYMENT_FAILED as a valid
# eventType filter value, which is reasonable evidence a failure event
# exists under that name, but it is not the same confirmation as
# seeing it in the webhook "common events" list.
#
# ACTION BEFORE THE DEMO: when registering the webhook on Nomba's real
# dashboard, there is an event-selection step ("select the events you
# will like to be notified on") — the literal event name shown there
# is the authoritative source, more reliable than either doc page.
# Confirm it there and update NOMBA_FAILURE_EVENT_TYPES (an env var,
# no redeploy needed) if it differs from "PAYMENT_FAILED".
# Alternatively, trigger a real sandbox failure using the documented
# test card (5060 6666 6666 6666 674 — the "insufficient funds" test
# card) and inspect what actually arrives.
#
# Matching is case-insensitive and checks both "event_type" and
# "event" as the field name (see services/signature.py's extract_event)
# since a training quiz payload used lowercase under "event" while
# this endpoint's own docs use uppercase under (presumably) "event_type"
# — neither source fully confirms the real production field name either.
#
# No dedicated "abandoned" event type appears in Nomba's documented
# list at all — USER_ABANDONED classification currently can only be
# reached via the AI-ambiguous-case path, not a dedicated event type.


def _normalize_event_type(event_type: str) -> str:
    return (event_type or "").strip().upper()


async def _run_classification(event_id: str):
    """Background task: classify a stored FailureEvent, then hand off
    to the (opt-in, hard-gated) automatic recovery path. Runs in its
    own short-lived DB session since BackgroundTasks execute after the
    request-scoped session has already closed."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        event = db.query(FailureEvent).filter(FailureEvent.id == event_id).first()
        if event is None:
            return
        raw = json.loads(event.raw_payload)
        result = classify_failure(
            response_code=event.response_code,
            transaction_type=event.transaction_type,
            event_type=event.event_type,
            amount=event.amount,
            currency=event.currency,
            raw_payload=raw,
        )
        event.classification = result.classification
        event.recovery_score = result.recovery_score
        event.recovery_message = result.recovery_message
        event.status = FailureStatus.CLASSIFIED
        db.add(event)
        db.commit()
        db.refresh(event)

        await maybe_auto_recover(event, db)
    except Exception:
        logger.exception("classification failed for event_id=%s", event_id)
    finally:
        db.close()


def _kobo_to_naira(amount_kobo) -> int:
    """Nomba's confirmed convention: amounts are in kobo. This system
    stores and displays naira throughout (matching the shared dashboard
    API contract), so the conversion happens once, right here at
    ingestion — nothing downstream needs to think about kobo."""
    try:
        return round(float(amount_kobo) / 100)
    except (TypeError, ValueError):
        return 0


@router.post("/webhooks/nomba")
async def receive_nomba_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    raw_body = await request.body()

    headers = dict(request.headers)
    try:
        verify_signature(
            raw_body=raw_body,
            headers=headers,
            secret=settings.NOMBA_WEBHOOK_SIGNATURE_KEY,
            signature_header=settings.NOMBA_SIGNATURE_HEADER,
        )
    except SignatureVerificationError as e:
        # Do not leak details of *why* verification failed beyond a
        # generic reason — never echo back the computed/expected
        # signature or the signing key.
        logger.warning("rejected webhook: signature verification failed (%s)", str(e))
        return JSONResponse(status_code=401, content={"detail": "unauthorized"})

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("rejected webhook: invalid JSON body")
        return JSONResponse(status_code=400, content={"detail": "invalid JSON"})

    verified = extract_event(payload)

    # Store the raw event_type as Nomba actually sent it (for
    # debugging/auditability), but compare using the normalized form
    # since the real casing convention is still unconfirmed — see the
    # note above FAILURE_EVENT_TYPES.
    event_type = verified.event_type
    normalized_event_type = _normalize_event_type(event_type)
    idempotency_key = f"{normalized_event_type}:{verified.transaction_id}:{verified.request_id}"

    # Idempotency at ingestion: duplicate delivery of the same event
    # returns 200 immediately without reprocessing.
    existing = (
        db.query(FailureEvent)
        .filter(FailureEvent.idempotency_key == idempotency_key)
        .first()
    )
    if existing is not None:
        return JSONResponse(status_code=200, content={"status": "duplicate_ignored"})

    if normalized_event_type in settings.NOMBA_SUCCESS_EVENT_TYPES:
        # Look for a FailureEvent whose recovery checkout this success
        # event might correspond to, then verify server-side before
        # flipping status — never trust the webhook payload alone.
        candidate = (
            db.query(FailureEvent)
            .filter(
                FailureEvent.recovery_checkout_order_id.isnot(None),
                FailureEvent.status == FailureStatus.RECOVERY_TRIGGERED,
            )
            .filter(
                FailureEvent.recovery_checkout_order_id
                == verified.transaction_id
            )
            .first()
        )
        if candidate is not None:
            await confirm_recovery_if_paid(candidate, db)
        return JSONResponse(status_code=200, content={"status": "ok"})

    if normalized_event_type not in settings.NOMBA_FAILURE_EVENT_TYPES:
        # Not a failure/abandonment/success event we care about for
        # this build's scope — acknowledge and ignore, don't 4xx (a
        # 4xx here would trigger Nomba's retry/backoff policy for an
        # event we were never going to process anyway).
        return JSONResponse(status_code=200, content={"status": "ignored"})

    event = FailureEvent(
        nomba_transaction_id=verified.transaction_id or "",
        request_id=verified.request_id,
        merchant_user_id=verified.merchant_user_id,
        wallet_id=verified.wallet_id,
        event_type=normalized_event_type,
        transaction_type=verified.transaction_type,
        amount=_kobo_to_naira(verified.amount_kobo),
        currency=verified.currency or "NGN",
        response_code=verified.response_code,
        customer_email=verified.customer_email,
        customer_phone=verified.customer_phone,
        customer_name=verified.customer_name,
        raw_payload=json.dumps(payload),
        status=FailureStatus.NEW,
        idempotency_key=idempotency_key,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    # Always return 200 quickly; classification (which may call Gemini)
    # runs as a background task so Nomba never waits on it.
    background_tasks.add_task(_run_classification, event.id)

    return JSONResponse(status_code=200, content={"status": "received", "id": event.id})
