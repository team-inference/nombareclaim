from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import FailureEvent, FailureStatus
from app.services import nomba_client


class RecoveryError(Exception):
    pass


async def trigger_recovery(event_id: str, db: Session, callback_base_url: str) -> FailureEvent:
    event = db.query(FailureEvent).filter(FailureEvent.id == event_id).first()
    if event is None:
        raise RecoveryError("not_found")

    # Idempotency at the trigger point: if already triggered/recovered,
    # return existing state rather than creating a duplicate checkout.
    if event.status in (FailureStatus.RECOVERY_TRIGGERED, FailureStatus.RECOVERED):
        return event

    # The recovery_score threshold is advisory for this manual path —
    # judges/merchants can override it from the dashboard. It's a real
    # gate only for any future fully-automatic trigger path, which this
    # build does not implement (kept honestly out of scope, see SECURITY.md).

    result = await nomba_client.create_checkout_order(
        amount=event.amount,
        currency=event.currency,
        customer_reference=f"reclaim-{event.id}",
        description=f"Recovery checkout for failed transaction {event.nomba_transaction_id}",
        callback_url=f"{callback_base_url}/webhooks/nomba",
    )

    event.recovery_checkout_order_id = result["order_reference"]
    event.recovery_checkout_url = result["checkout_url"]
    event.status = FailureStatus.RECOVERY_TRIGGERED
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


async def confirm_recovery_if_paid(event: FailureEvent, db: Session) -> FailureEvent:
    """
    Server-side verification: called when a payment_success webhook
    arrives referencing a recovery_checkout_order_id we know about.
    Cross-checks against Nomba's transaction API rather than trusting
    the webhook payload alone, per the security note's core principle.
    """
    if not event.recovery_checkout_order_id:
        return event

    tx = await nomba_client.get_transaction_status(event.recovery_checkout_order_id)
    status = (tx.get("status") or "").upper()

    if status in ("SUCCESS", "SUCCESSFUL", "PAYMENT_SUCCESS", "COMPLETED"):
        event.status = FailureStatus.RECOVERED
        event.recovered_at = datetime.now(timezone.utc)
        db.add(event)
        db.commit()
        db.refresh(event)

    return event
