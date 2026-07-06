import asyncio
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal, init_db
from app.models import FailureEvent, FailureStatus, Classification
from app.services import nomba_client
from app.services.scheduler import run_retry_sweep_once

init_db()


async def _fake_create_checkout_order(**kwargs):
    return {
        "checkout_url": "https://sandbox.nomba.example/checkout/retry-order",
        "order_reference": kwargs["customer_reference"],
    }


def test_retry_sweep_processes_due_events_and_reschedules(monkeypatch):
    monkeypatch.setattr(nomba_client, "create_checkout_order", _fake_create_checkout_order)

    db = SessionLocal()
    try:
        past_due = FailureEvent(
            nomba_transaction_id="txn-scheduler-due",
            request_id="req-scheduler-due",
            event_type="PAYMENT_FAILED",
            amount=5000,
            currency="NGN",
            customer_email="scheduler-test@example.com",
            classification=Classification.CARD_DECLINED,
            recovery_score=50,
            status=FailureStatus.CLASSIFIED,
            next_retry_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            idempotency_key="scheduler-test-due-key",
            raw_payload="{}",
        )
        not_due = FailureEvent(
            nomba_transaction_id="txn-scheduler-not-due",
            request_id="req-scheduler-not-due",
            event_type="PAYMENT_FAILED",
            amount=5000,
            currency="NGN",
            customer_email="scheduler-test-2@example.com",
            classification=Classification.CARD_DECLINED,
            recovery_score=50,
            status=FailureStatus.CLASSIFIED,
            next_retry_at=datetime.now(timezone.utc) + timedelta(hours=5),
            idempotency_key="scheduler-test-not-due-key",
            raw_payload="{}",
        )
        db.add_all([past_due, not_due])
        db.commit()
        db.refresh(past_due)
        db.refresh(not_due)
        due_id, not_due_id = past_due.id, not_due.id
    finally:
        db.close()

    processed = asyncio.run(run_retry_sweep_once())
    assert processed >= 1

    db = SessionLocal()
    try:
        refreshed_due = db.query(FailureEvent).filter(FailureEvent.id == due_id).first()
        refreshed_not_due = db.query(FailureEvent).filter(FailureEvent.id == not_due_id).first()

        assert refreshed_due.status == FailureStatus.RECOVERY_TRIGGERED
        assert refreshed_due.retry_count == 1
        assert refreshed_due.recovery_checkout_url is not None

        # Untouched — its next_retry_at was still in the future.
        assert refreshed_not_due.retry_count == 0
        assert refreshed_not_due.status == FailureStatus.CLASSIFIED
    finally:
        db.close()
