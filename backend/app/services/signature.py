"""
Nomba webhook HMAC signature verification.

Field order and the colon-joined construction here follow the pattern
documented at developer.nomba.com/products/webhooks/signature-verification-new
and the real webhook payload shape published at
developer.nomba.com/products/webhooks/introduction, which confirms the
payload carries event_type, requestId, data.merchant.{userId,walletId}
and data.transaction.{transactionId,type,time,responseCode}.

What was NOT independently confirmable from public docs (the sample-code
tabs on that page render client-side and weren't fetchable): the exact
literal header names Nomba uses to carry the signature and the request
timestamp. Do not guess past this point — send one real test webhook
from the Nomba dashboard (Webhooks > your webhook > Logs, or "Send test
event" if available) and read the literal header names off that
delivery. Then set NOMBA_SIGNATURE_HEADER / NOMBA_TIMESTAMP_HEADER in
.env to match — no code change needed. Until confirmed, this defaults
to "signature" and "timestamp", which are Nomba's own field names for
these two concepts elsewhere in their docs, but treat that as a
best-guess default, not a verified fact, and say so plainly in the
SECURITY.md / demo if asked.
"""
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Optional


class SignatureVerificationError(Exception):
    """Raised with a short, non-leaky reason. Never include the computed
    or expected signature value in this message — that would itself be
    an information leak to a misbehaving caller."""


@dataclass
class VerifiedEvent:
    event_type: str
    request_id: str
    merchant_user_id: Optional[str]
    wallet_id: Optional[str]
    transaction_id: Optional[str]
    transaction_type: Optional[str]
    transaction_time: Optional[str]
    response_code: Optional[str]


def _safe_get(d: dict, *path, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def build_signing_string(payload: dict, timestamp: str) -> str:
    """
    Colon-joined string of, in order:
    event_type : requestId : data.merchant.userId : data.merchant.walletId
    : data.transaction.transactionId : data.transaction.type
    : data.transaction.time : data.transaction.responseCode : <timestamp>

    Missing fields are represented as empty strings rather than the
    literal "None" so the hash is stable and reproducible by hand for
    the unit test.
    """
    fields = [
        payload.get("event_type", ""),
        payload.get("requestId", ""),
        _safe_get(payload, "data", "merchant", "userId", default=""),
        _safe_get(payload, "data", "merchant", "walletId", default=""),
        _safe_get(payload, "data", "transaction", "transactionId", default=""),
        _safe_get(payload, "data", "transaction", "type", default=""),
        _safe_get(payload, "data", "transaction", "time", default=""),
        _safe_get(payload, "data", "transaction", "responseCode", default=""),
    ]
    fields = [str(f) if f is not None else "" for f in fields]
    return ":".join(fields) + ":" + str(timestamp)


def compute_signature(payload: dict, timestamp: str, signature_key: str) -> str:
    message = build_signing_string(payload, timestamp)
    digest = hmac.new(
        key=signature_key.encode("utf-8"),
        msg=message.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
    return digest


def verify_nomba_signature(
    payload: dict,
    headers: dict,
    signature_key: str,
    signature_header: str = "signature",
    timestamp_header: str = "timestamp",
    replay_window_seconds: int = 300,
) -> VerifiedEvent:
    """
    Raises SignatureVerificationError on any failure. Returns a
    VerifiedEvent with the extracted fields on success. Headers dict is
    expected lowercased (FastAPI's Headers object is already
    case-insensitive on lookup, but we normalize defensively here since
    Nomba's own docs note header names are case-insensitive).
    """
    headers = {k.lower(): v for k, v in headers.items()}

    received_signature = headers.get(signature_header.lower())
    timestamp = headers.get(timestamp_header.lower())

    if not received_signature:
        raise SignatureVerificationError("missing signature header")
    if not timestamp:
        raise SignatureVerificationError("missing timestamp header")
    if not signature_key:
        raise SignatureVerificationError("server misconfigured: no signature key set")

    # Replay protection: reject anything outside the freshness window.
    try:
        ts_value = float(timestamp)
        # Nomba timestamps could plausibly be seconds or milliseconds;
        # normalize milliseconds down to seconds if it looks too large
        # to be a sane Unix-seconds value.
        if ts_value > 1e12:
            ts_value = ts_value / 1000.0
    except (TypeError, ValueError):
        raise SignatureVerificationError("invalid timestamp header")

    now = time.time()
    if abs(now - ts_value) > replay_window_seconds:
        raise SignatureVerificationError("timestamp outside replay window")

    expected = compute_signature(payload, timestamp, signature_key)

    if not hmac.compare_digest(expected, received_signature):
        raise SignatureVerificationError("signature mismatch")

    return VerifiedEvent(
        event_type=payload.get("event_type", ""),
        request_id=payload.get("requestId", ""),
        merchant_user_id=_safe_get(payload, "data", "merchant", "userId"),
        wallet_id=_safe_get(payload, "data", "merchant", "walletId"),
        transaction_id=_safe_get(payload, "data", "transaction", "transactionId"),
        transaction_type=_safe_get(payload, "data", "transaction", "type"),
        transaction_time=_safe_get(payload, "data", "transaction", "time"),
        response_code=_safe_get(payload, "data", "transaction", "responseCode"),
    )
