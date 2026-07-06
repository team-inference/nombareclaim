"""
Pure, dependency-free scheduling logic for automatic recovery
follow-ups. Kept separate from recovery.py/scheduler.py so the actual
date-math can be unit tested with no database, no network call, and
no FastAPI app context.

Two follow-up strategies:

- INSUFFICIENT_FUNDS ("payday retry"): retried around Nigeria's common
  salary-payment window rather than a fixed short delay, since a
  wallet that was empty is far more likely to succeed once the
  customer has actually been paid than it is three hours later.
  Configurable via PAYDAY_RETRY_DAYS (default: the 25th through the
  end of the month, plus the 1st).
- Everything else: a short fixed backoff schedule
  (RETRY_BACKOFF_HOURS, default 3h / 24h / 72h) — these failures
  (card declined, network timeout, abandoned) aren't tied to a
  predictable future event the way an empty wallet is.

Both strategies stop scheduling further attempts once
MAX_AUTO_RETRIES is reached, so a customer is never emailed forever.
"""
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import settings
from app.models import Classification


def next_payday_datetime(now: datetime, payday_days: list[int]) -> datetime:
    """Next datetime, strictly after `now`, whose day-of-month is in
    payday_days (or is the last day of a shorter month and
    `days_in_month` is itself in payday_days — e.g. Feb 28 counts as
    "the 30th" for a short month). Walks forward day by day, which is
    simple and obviously correct; this runs at most once per retry,
    never in a hot loop, so there's no reason to be clever here."""
    candidate = now
    for _ in range(40):
        candidate = candidate + timedelta(days=1)
        days_in_month = monthrange(candidate.year, candidate.month)[1]
        day = candidate.day
        if day in payday_days or (day == days_in_month and days_in_month in payday_days):
            return candidate.replace(hour=8, minute=0, second=0, microsecond=0)
    # Unreachable with any sane payday_days list (there's always a
    # matching day within 31 days) — kept only so this can never loop
    # forever or raise.
    return now + timedelta(days=7)


def compute_next_retry(
    classification: Optional[Classification],
    retry_count: int,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    """Returns the next UTC datetime to retry at, or None if no further
    retry should be scheduled (max retries reached)."""
    if retry_count >= settings.MAX_AUTO_RETRIES:
        return None

    now = now or datetime.now(timezone.utc)

    if classification == Classification.INSUFFICIENT_FUNDS:
        return next_payday_datetime(now, settings.PAYDAY_RETRY_DAYS)

    hours_schedule = settings.RETRY_BACKOFF_HOURS
    if retry_count < len(hours_schedule):
        return now + timedelta(hours=hours_schedule[retry_count])
    return None
