"""
Simple in-memory sliding-window rate limiter. Sufficient for hackathon
scope (single process, single Render instance) — no Redis needed.
Applied to the trigger-recovery endpoint to stop a burst of manual
clicks or retried webhook-driven triggers from spamming recovery
checkouts for the same customer.

NOTE: this is per-process memory, so it resets on every redeploy/restart
and does not coordinate across multiple instances. Documented as a
known limitation in SECURITY.md, not hidden.
"""
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request

from app.config import settings

_hits: Dict[str, Deque[float]] = defaultdict(deque)


def _client_key(request: Request, extra: str = "") -> str:
    ip = request.client.host if request.client else "unknown"
    return f"{ip}:{extra}"


def check_rate_limit(request: Request, key_suffix: str = "") -> None:
    key = _client_key(request, key_suffix)
    now = time.time()
    window = settings.RATE_LIMIT_WINDOW_SECONDS
    bucket = _hits[key]

    while bucket and now - bucket[0] > window:
        bucket.popleft()

    if len(bucket) >= settings.RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(status_code=429, detail="Too many requests, slow down.")

    bucket.append(now)
