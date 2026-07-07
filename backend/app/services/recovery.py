import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session
from app.config import settings

from app.models import FailureEvent, FailureStatus
from app.services import nomba_client, scheduling
from app.services.notifications import send_recovery_email
from app.services.signature import _safe_get

logger = logging.getLogger("nombareclaim.recovery")


class RecoveryError(Exception):
    pass


def _build_recovery_email(event: FailureEvent) -> tuple[str, str]:
    subject = "Your payment didn't go through — here's a quick link to finish it"
    message = event.recovery_message or (
        f"Your payment of {event.currency} {event.amount:,} didn't go through. "
        "Here's a fresh link to complete it whenever you're ready."
    )
    body = (
        f"{message}\n\n"
        f"Complete your payment here: {event.recovery_checkout_url}\n\n"
        "This link was sent automatically by NombaReclaim on behalf of the merchant."
    )
    return subject, body


async def trigger_recovery(event_id: str, db: Session, callback_base_url: str) -> FailureEvent:
    event = db.query(FailureEvent).filter(FailureEvent.id == event_id).first()
    if event is None:
        raise RecoveryError("not_found")

    # Idempotency at the trigger point: if already triggered/recovered,
    # return existing state rather than creating a duplicate checkout.
    if event.status in (FailureStatus.RECOVERY_TRIGGERED, FailureStatus.RECOVERED):
        return event

    # The recovery_score threshold is advisory for this manual path —
    # judges/merchants can override it from the dashboard. It's a real,
    # hard gate only for the fully-automatic path (see
    # maybe_auto_recover below), consistent with SECURITY.md.

    result = await nomba_client.create_checkout_order(
        amount=event.amount,
        currency=event.currency,
        customer_reference=f"reclaim-{event.id}",
        description=f"Recovery checkout for failed transaction {event.nomba_transaction_id}",
        callback_url=f"{settings.FRONTEND_BASE_URL}/payment/return",
        customer_email=event.customer_email,
    )

    event.recovery_checkout_order_id = result["order_reference"]
    event.recovery_checkout_url = result["checkout_url"]
    event.status = FailureStatus.RECOVERY_TRIGGERED
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


async def maybe_auto_recover(event: FailureEvent, db: Session) -> FailureEvent:
    """
    Called right after classification finishes. This IS the
    fully-automatic path referenced (and deliberately left
    unimplemented) in earlier versions of this file — the
    recovery_score threshold is a hard gate here, unlike the manual
    dashboard "trigger recovery" button.

    Fires only when all of the following hold:
    - RECOVERY_AUTOMATION_ENABLED is on (off by default — a fresh
      deploy never silently emails a real customer)
    - the event actually has a customer_email captured from the
      webhook payload (not guaranteed to exist — see signature.py)
    - recovery_score clears AUTO_RECOVERY_MIN_SCORE
    - the event hasn't already had recovery triggered

    Generates a checkout link exactly like the manual path, then sends
    a recovery email and schedules the next automatic follow-up
    ("payday retry" for insufficient funds, short fixed backoff
    otherwise) via services/scheduling.py.
    """
    if not settings.RECOVERY_AUTOMATION_ENABLED:
        return event
    if not event.customer_email:
        return event
    if event.status != FailureStatus.CLASSIFIED:
        return event
    if (event.recovery_score or 0) < settings.AUTO_RECOVERY_MIN_SCORE:
        return event

    try:
        event = await trigger_recovery(event_id=event.id, db=db, callback_base_url="")
    except RecoveryError:
        return event
    except nomba_client.NombaAPIError:
        logger.exception("auto-recovery checkout creation failed for event_id=%s", event.id)
        return event

    subject, body = _build_recovery_email(event)
    sent = send_recovery_email(event.customer_email, subject, body)

    event.last_notified_at = datetime.now(timezone.utc)
    if sent:
        event.next_retry_at = scheduling.compute_next_retry(
            event.classification, event.retry_count, datetime.now(timezone.utc)
        )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


async def send_retry_recovery(event: FailureEvent, db: Session) -> FailureEvent:
    """
    Called by the background retry sweep (services/scheduler.py) when
    an event's next_retry_at has come due. Generates a FRESH checkout
    order (the first one may well have expired by now) with a unique
    order reference per attempt, re-sends the recovery email, and
    reschedules the following attempt if any remain.
    """
    if event.status == FailureStatus.RECOVERED:
        return event

    try:
        result = await nomba_client.create_checkout_order(
            amount=event.amount,
            currency=event.currency,
            customer_reference=f"reclaim-{event.id}-r{event.retry_count + 1}",
            description=f"Recovery checkout retry for failed transaction {event.nomba_transaction_id}",
            callback_url=f"{settings.FRONTEND_BASE_URL}/payment/return",
            customer_email=event.customer_email,
        )
    except Exception:
        logger.exception("retry checkout creation failed for event_id=%s", event.id)
        return event

    event.recovery_checkout_order_id = result["order_reference"]
    event.recovery_checkout_url = result["checkout_url"]
    event.status = FailureStatus.RECOVERY_TRIGGERED

    subject, body = _build_recovery_email(event)
    sent = send_recovery_email(event.customer_email, subject, body)

    event.retry_count += 1
    event.last_notified_at = datetime.now(timezone.utc)
    event.next_retry_at = (
        scheduling.compute_next_retry(event.classification, event.retry_count, datetime.now(timezone.utc))
        if sent
        # If the send itself failed (mail server hiccup, not a
        # scheduling decision), try again soon rather than waiting for
        # the full next backoff step — this doesn't count against
        # MAX_AUTO_RETRIES since retry_count already incremented above
        # reflects an attempted checkout link, not a confirmed send.
        else datetime.now(timezone.utc) + timedelta(hours=1)
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


async def confirm_recovery_if_paid(event: FailureEvent, db: Session) -> FailureEvent:
    """
    Server-side verification: called when a payment_success webhook
    arrives referencing a recovery_checkout_order_id we know about.
    Cross-checks against Nomba's own checkout order status endpoint
    rather than trusting the webhook payload alone, per the security
    note's core principle — a forged or replayed webhook cannot move
    this system's state on its own, only trigger a lookup.

    CORRECTED: the official developer.nomba.com sandbox-testing doc's
    real "verify a transaction" example confirms the response shape:
    `data.success` (boolean) and `data.message` /
    `data.transactionDetails.statusCode` (both text, observed value
    "PAYMENT SUCCESSFUL"). An earlier version of this function didn't
    have this confirmed shape and checked several generic guessed
    field names instead (status/orderStatus/paymentStatus) — those are
    kept as a fallback only, in case a real response ever differs from
    this doc's specific example.
    """
    if not event.recovery_checkout_order_id:
        return event

    order = await nomba_client.get_checkout_order_status(event.recovery_checkout_order_id)

    # Confirmed primary signal: an explicit boolean.
    if order.get("success") is True:
        event.status = FailureStatus.RECOVERED
        event.recovered_at = datetime.now(timezone.utc)
        db.add(event)
        db.commit()
        db.refresh(event)
        return event
    if order.get("success") is False:
        return event

    # Confirmed secondary signal: text status, either at the top level
    # or nested under transactionDetails.statusCode per the real
    # example. Falls back to older guessed field names only if neither
    # confirmed field is present at all (e.g. a differently-shaped
    # production response).
    status_value = (
        order.get("message")
        or _safe_get(order, "transactionDetails", "statusCode")
        or order.get("status")
        or order.get("orderStatus")
        or order.get("paymentStatus")
        or ""
    )
    status_value = str(status_value).upper()

    if status_value in (
        "PAYMENT SUCCESSFUL",
        "SUCCESS",
        "SUCCESSFUL",
        "PAYMENT_SUCCESS",
        "COMPLETED",
        "PAID",
    ):
        event.status = FailureStatus.RECOVERED
        event.recovered_at = datetime.now(timezone.utc)
        db.add(event)
        db.commit()
        db.refresh(event)

    return event
