from datetime import datetime, timezone

from app.config import settings
from app.models import Classification
from app.services.scheduling import compute_next_retry, next_payday_datetime


def test_insufficient_funds_schedules_next_payday_window_day():
    now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)  # a Monday, mid-month
    result = compute_next_retry(Classification.INSUFFICIENT_FUNDS, retry_count=0, now=now)
    assert result is not None
    assert result.day in settings.PAYDAY_RETRY_DAYS
    assert result > now


def test_next_payday_datetime_wraps_into_next_month_correctly():
    # Starting mid-month, the next matching day should be later this
    # month or, if none remain, the 1st of next month.
    now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    result = next_payday_datetime(now, [25])
    assert result.day == 25
    assert result.month == 7


def test_other_classifications_use_fixed_backoff_schedule():
    now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    first = compute_next_retry(Classification.CARD_DECLINED, retry_count=0, now=now)
    second = compute_next_retry(Classification.CARD_DECLINED, retry_count=1, now=now)

    assert first is not None and second is not None
    # First backoff step should be shorter than the second.
    assert (first - now) < (second - now)


def test_retry_stops_after_max_retries_reached():
    now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    result = compute_next_retry(
        Classification.CARD_DECLINED, retry_count=settings.MAX_AUTO_RETRIES, now=now
    )
    assert result is None


def test_unclassified_none_falls_back_to_fixed_backoff():
    now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    result = compute_next_retry(None, retry_count=0, now=now)
    assert result is not None
    assert result > now
