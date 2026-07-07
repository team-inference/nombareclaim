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
# confirm a recovery in progress. Both are configurable via
# NOMBA_FAILURE_EVENT_TYPES / NOMBA_SUCCESS_EVENT_TYPES (see
# config.py) rather than hardcoded here.
#
# CORRECTED: Nomba's real, official developer docs
# (developer.nomba.com/docs/products/accept-payment/sandbox-testing)
# were located after the training-certification quiz was first treated
# as the confirmed source. The official doc's real payment_success
# webhook example uses `"event_type": "payment_success"` (lowercase
# value, field name "event_type") — this settles the earlier
# uncertainty about field name/casing in favor of "event_type", though
# matching here still stays case-insensitive as cheap extra safety.
#
# STILL genuinely unresolved: the official doc's example is for
# payment_success — it does not show a failed-payment example at all,
# so the real event name for a failed payment remains unconfirmed by
# ANY source seen so far, official or training quiz. Confirm it by
# triggering a real sandbox failure with the documented "do not honor"
# decline test card (5484497218317651 per the official sandbox-testing
# doc) and inspecting what actually arrives, or by asking Nomba/
# DevCareer directly what event name their webhook-forwarding for this
# hackathon actually sends.


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
    """Training-quiz-confirmed convention: amounts are in kobo. Still
    used as a fallback path — see _resolve_amount_naira below for why
    this is no longer the only path."""
    try:
        return round(float(amount_kobo) / 100)
    except (TypeError, ValueError):
        return 0


def _resolve_amount_naira(verified) -> int:
    """
    Reconciles the two confirmed-but-conflicting amount unit
    conventions found across two different Nomba doc sources (see
    services/signature.py's extract_event docstring for the full
    explanation):

    - The official developer.nomba.com sandbox-testing doc's real
      webhook example (`data.order.amount` /
      `data.transaction.transactionAmount`) is already in NAIRA.
    - The training-certification quiz's signature-lab example
      (`data.amount`) is in KOBO.

    Prefers the officially-confirmed naira value when present (it's
    from a real, current API reference); falls back to the kobo
    conversion only when that field is absent, on the assumption that
    a payload using the flat/training shape is following that shape's
    kobo convention too.
    """
    if verified.amount_naira is not None:
        try:
            return round(float(verified.amount_naira))
        except (TypeError, ValueError):
            pass
    return _kobo_to_naira(verified.amount_kobo)


@router.post("/webhooks/nomba")
async def receive_nomba_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    raw_body = await request.body()
    headers = dict(request.headers)

    # CORRECTED: Nomba's real signature scheme signs specific fields
    # from the PARSED payload plus the nomba-timestamp header value —
    # not a hash of the raw body (see services/signature.py). That
    # means JSON parsing now has to happen before signature
    # verification, the reverse of the previous (incorrect) ordering.
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("rejected webhook: invalid JSON body")
        return JSONResponse(status_code=400, content={"detail": "invalid JSON"})

    try:
        verify_signature(
            payload=payload,
            headers=headers,
            secret=settings.NOMBA_WEBHOOK_SIGNATURE_KEY,
            signature_header=settings.NOMBA_SIGNATURE_HEADER,
            timestamp_header=settings.NOMBA_TIMESTAMP_HEADER,
        )
    except SignatureVerificationError as e:
        # Do not leak details of *why* verification failed beyond a
        # generic reason — never echo back the computed/expected
        # signature or the signing key.
        logger.warning("rejected webhook: signature verification failed (%s)", str(e))
        return JSONResponse(status_code=401, content={"detail": "unauthorized"})

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
        #
        # CORRECTED: match on data.order.orderReference (the confirmed
        # real field, and literally the value we ourselves set when
        # creating the recovery checkout — see services/recovery.py),
        # not transaction_id. An earlier version matched on
        # transaction_id, which was never actually the right field for
        # this purpose; kept as a fallback only for payloads following
        # the older flat/training-quiz shape that has no separate order
        # object at all.
        match_reference = verified.order_reference or verified.transaction_id
        candidate = (
            db.query(FailureEvent)
            .filter(
                FailureEvent.recovery_checkout_order_id.isnot(None),
                FailureEvent.status == FailureStatus.RECOVERY_TRIGGERED,
            )
            .filter(
                FailureEvent.recovery_checkout_order_id
                == match_reference
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
        amount=_resolve_amount_naira(verified),
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
