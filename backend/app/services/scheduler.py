"""
Background retry sweep.

A single asyncio task, started at app startup (see main.py) only when
RECOVERY_AUTOMATION_ENABLED is on. Wakes up every
RETRY_SWEEP_INTERVAL_SECONDS, finds every FailureEvent whose
next_retry_at has come due, and hands each one to
services.recovery.send_retry_recovery.

Deliberately a plain asyncio loop rather than APScheduler/Celery/a
cron service — this is a single-instance Railway deployment with one
worker process, so a lightweight in-process loop is simpler to reason
about and ship than adding a scheduling dependency + a separate broker
for one periodic sweep. If this ever runs across multiple instances,
this should move to a real job queue so two workers can't double-send
the same retry — noted here rather than silently assumed away.
"""
import asyncio
import logging
from datetime import datetime, timezone

from app.config import settings
from app.database import SessionLocal
from app.models import FailureEvent, FailureStatus
from app.services.recovery import send_retry_recovery

logger = logging.getLogger("nombareclaim.scheduler")


async def run_retry_sweep_once() -> int:
    """Runs a single sweep. Returns the number of events processed.
    Exposed separately from the loop so it can be called directly in
    tests without waiting on a sleep interval."""
    db = SessionLocal()
    processed = 0
    try:
        now = datetime.now(timezone.utc)
        due = (
            db.query(FailureEvent)
            .filter(
                FailureEvent.next_retry_at.isnot(None),
                FailureEvent.next_retry_at <= now,
                FailureEvent.status.in_(
                    [FailureStatus.CLASSIFIED, FailureStatus.RECOVERY_TRIGGERED]
                ),
                FailureEvent.customer_email.isnot(None),
            )
            .all()
        )
        for event in due:
            try:
                await send_retry_recovery(event, db)
                processed += 1
            except Exception:
                logger.exception("retry send failed for event_id=%s", event.id)
    finally:
        db.close()
    return processed


async def retry_sweep_loop() -> None:
    logger.info(
        "retry sweep loop started (interval=%ss)", settings.RETRY_SWEEP_INTERVAL_SECONDS
    )
    while True:
        try:
            count = await run_retry_sweep_once()
            if count:
                logger.info("retry sweep processed %d due event(s)", count)
        except Exception:
            logger.exception("retry sweep iteration failed")
        await asyncio.sleep(settings.RETRY_SWEEP_INTERVAL_SECONDS)
