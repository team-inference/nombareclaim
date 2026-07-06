"""
Nomba webhook signature verification.

CONFIRMED against Nomba's own official training documentation
(training.nomba.com, "Webhooks" module) — this is no longer a
best-guess implementation, it matches their documented Node.js
reference sample exactly:

    const signature = req.header("nomba-signature");
    const expected = crypto
      .createHmac("sha256", process.env.NOMBA_WEBHOOK_SECRET)
      .update(req.body)      // the RAW request body, not a
      .digest("hex");        // reconstructed/reserialized payload

    if (signature !== expected) return res.status(401).send("bad signature");

Two things this confirms and corrects versus an earlier draft of this
file:
1. There is exactly ONE header (`nomba-signature`), not a separate
   signature + timestamp pair. Nomba's own sample does not implement
   timestamp-based replay protection at all.
2. The HMAC is computed over the raw, unparsed request body bytes —
   NOT a colon-joined reconstruction of individual JSON fields. This
   matters: hashing a re-serialized version of the parsed JSON can
   produce a different byte sequence than what was actually sent
   (whitespace, key order), which would make a genuinely valid
   signature appear invalid. The raw body must be captured before any
   JSON parsing happens, and hashed as-is.

Replay protection: Nomba's own documentation recommends idempotency
via `event.requestId` — ignore an event if that requestId has already
been processed — rather than a timestamp freshness window. This
system implements exactly that (see app/routes/webhooks.py's
idempotency_key check), so no separate timestamp-based replay check is
needed or attempted here.
"""
import hashlib
import hmac
from dataclasses import dataclass
from typing import Optional


class SignatureVerificationError(Exception):
    """Raised with a short, non-leaky reason. Never include the computed
    or expected signature value in this message — that would itself be
    an information leak to a misbehaving caller."""


@dataclass
class ParsedEvent:
    event_type: str
    request_id: str
    merchant_tx_ref: Optional[str]
    merchant_user_id: Optional[str]
    wallet_id: Optional[str]
    transaction_id: Optional[str]
    transaction_type: Optional[str]
    transaction_time: Optional[str]
    response_code: Optional[str]
    amount_kobo: Optional[str]
    currency: Optional[str]
    customer_email: Optional[str]
    customer_phone: Optional[str]
    customer_name: Optional[str]


def _safe_get(d: dict, *path, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def verify_signature(
    raw_body: bytes,
    headers: dict,
    secret: str,
    signature_header: str = "nomba-signature",
) -> None:
    """
    Raises SignatureVerificationError on any failure. Returns nothing —
    a clean return means the signature is valid. Must be called with
    the RAW bytes exactly as received, before any JSON parsing.
    """
    headers = {k.lower(): v for k, v in headers.items()}
    received_signature = headers.get(signature_header.lower())

    if not received_signature:
        raise SignatureVerificationError("missing signature header")
    if not secret:
        raise SignatureVerificationError("server misconfigured: no webhook secret set")

    expected = hmac.new(key=secret.encode("utf-8"), msg=raw_body, digestmod=hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, received_signature):
        raise SignatureVerificationError("signature mismatch")


def extract_event(payload: dict) -> ParsedEvent:
    """Pulls the fields this system needs out of an already-verified,
    already-JSON-parsed payload. Kept separate from verify_signature()
    since verification must happen on raw bytes, while field extraction
    naturally happens on the parsed dict afterwards.

    Two payload shapes are checked, since no fully-confirmed shape
    exists yet for the specific event this project cares about most
    (a failed payment):

    - FLAT (confirmed by Nomba's own training material, shown for a
      payment_success example): data.merchantTxRef, data.amount,
      data.currency, directly under data.
    - NESTED (an earlier, unconfirmed guess): data.transaction.*,
      data.merchant.*.

    The flat shape is tried first since it's the one actually
    confirmed by official material; the nested shape is kept as a
    fallback in case a failure-specific event turns out to carry
    richer nested detail (e.g. responseCode) that the flat
    payment_success example didn't need to show. event_type is read
    from either "event_type" or "event" for the same reason — see
    routes/webhooks.py's FAILURE_EVENT_TYPES comment for the full
    context on what's confirmed versus still open.
    """
    flat_amount = _safe_get(payload, "data", "amount")
    nested_amount = _safe_get(payload, "data", "transaction", "amount")

    return ParsedEvent(
        event_type=payload.get("event_type") or payload.get("event") or "",
        request_id=payload.get("requestId", ""),
        merchant_tx_ref=_safe_get(payload, "data", "merchantTxRef"),
        merchant_user_id=_safe_get(payload, "data", "merchant", "userId"),
        wallet_id=_safe_get(payload, "data", "merchant", "walletId"),
        transaction_id=(
            _safe_get(payload, "data", "merchantTxRef")
            or _safe_get(payload, "data", "transaction", "transactionId")
        ),
        transaction_type=_safe_get(payload, "data", "transaction", "type"),
        transaction_time=_safe_get(payload, "data", "transaction", "time"),
        response_code=_safe_get(payload, "data", "transaction", "responseCode"),
        amount_kobo=flat_amount if flat_amount is not None else nested_amount,
        currency=_safe_get(payload, "data", "currency") or _safe_get(payload, "data", "transaction", "currency"),
        # Not present in Nomba's confirmed payment_success example, and
        # not guaranteed to exist on a failure event either — checked
        # defensively across every plausible field name/nesting so the
        # automated recovery-notification pipeline can use it when it
        # IS there, without assuming it always will be.
        customer_email=(
            _safe_get(payload, "data", "customerEmail")
            or _safe_get(payload, "data", "customer", "email")
            or _safe_get(payload, "data", "email")
        ),
        customer_phone=(
            _safe_get(payload, "data", "customerPhone")
            or _safe_get(payload, "data", "customer", "phone")
            or _safe_get(payload, "data", "customer", "phoneNumber")
            or _safe_get(payload, "data", "phoneNumber")
        ),
        customer_name=(
            _safe_get(payload, "data", "customerName")
            or _safe_get(payload, "data", "customer", "name")
        ),
    )
