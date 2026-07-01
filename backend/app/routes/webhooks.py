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
from app.services.recovery import confirm_recovery_if_paid

logger = logging.getLogger("nombareclaim.webhooks")

router = APIRouter()

# Event types that represent a failed/abandoned payment and should be
# stored as a recoverable FailureEvent. Confirm the exact literal event
# type strings Nomba sends against the dashboard's "available events"
# list when registering the webhook (Phase J) — these are the
# documented/most-likely names based on the public webhook intro page
# (which confirms "payment_success" as the success-side name).
FAILURE_EVENT_TYPES = {
    "payment_failed",
    "payment_failure",
    "collection_failed",
    "payment_abandoned",
}

SUCCESS_EVENT_TYPES = {"payment_success"}


def _run_classification(event_id: str):
    """Background task: classify a stored FailureEvent. Runs in its own
    short-lived DB session since BackgroundTasks execute after the
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
    except Exception:
        logger.exception("classification failed for event_id=%s", event_id)
    finally:
        db.close()


def _parse_amount(payload: dict) -> int:
    raw = (
        payload.get("data", {})
        .get("transaction", {})
        .get("amount")
    )
    try:
        return int(round(float(raw)))
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

    event_type = verified.event_type
    idempotency_key = f"{event_type}:{verified.transaction_id}:{verified.request_id}"

    # Idempotency at ingestion: duplicate delivery of the same event
    # returns 200 immediately without reprocessing.
    existing = (
        db.query(FailureEvent)
        .filter(FailureEvent.idempotency_key == idempotency_key)
        .first()
    )
    if existing is not None:
        return JSONResponse(status_code=200, content={"status": "duplicate_ignored"})

    if event_type in SUCCESS_EVENT_TYPES:
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

    if event_type not in FAILURE_EVENT_TYPES:
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
        event_type=event_type,
        transaction_type=verified.transaction_type,
        amount=_parse_amount(payload),
        currency=payload.get("data", {}).get("transaction", {}).get("currency", "NGN"),
        response_code=verified.response_code,
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
