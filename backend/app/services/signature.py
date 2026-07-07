"""
Nomba webhook signature verification.

CORRECTION (second pass) — the previous version of this file, and the
"sandbox-testing" doc it was checked against, both assumed a plain
HMAC-SHA256-hex over the RAW request body. That is wrong. Nomba's own
dedicated Webhooks reference doc
(developer.nomba.com/docs/api-basics/webhook) gives the actual
reference implementation, and it's a completely different scheme:

- NOT a hash of the raw body. Instead, a colon-joined string built
  from specific fields pulled out of the ALREADY-PARSED payload, in
  this exact order:
    event_type : requestId : data.merchant.userId :
    data.merchant.walletId : data.transaction.transactionId :
    data.transaction.type : data.transaction.time :
    data.transaction.responseCode : <nomba-timestamp header value>
  Any missing field is treated as an empty string, not omitted — the
  colon separators still all appear.
- HMAC-SHA256 over that string, then **Base64-encoded** — not hex.
- Compared against the `nomba-signature` header, case-insensitively,
  using `hmac.compare_digest`.
- The `nomba-timestamp` header is not optional or advisory — its
  value is one of the fields actually hashed. A request missing that
  header can never produce a matching signature and must be rejected.

Practical consequence: since the fields being signed come from the
parsed payload, JSON parsing must now happen BEFORE signature
verification (previously the reverse, when we were hashing raw
bytes). See app/routes/webhooks.py for the updated ordering.

Still true and unchanged:
- Replay protection is via `event.requestId` idempotency (see
  app/routes/webhooks.py), not a timestamp freshness window — Nomba's
  own docs don't implement one either.
"""
import base64
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
    # Two DIFFERENT unit conventions were found confirmed in two
    # different real sources — kept as separate fields rather than
    # merged, so a caller never accidentally divides an
    # already-naira value by 100 (or vice versa). See amount_kobo vs
    # amount_naira docstring note on extract_event below.
    amount_kobo: Optional[str]
    amount_naira: Optional[str]
    currency: Optional[str]
    customer_email: Optional[str]
    customer_phone: Optional[str]
    customer_name: Optional[str]
    # Confirmed real field (data.order.orderReference) — this is
    # literally the value NombaReclaim itself sets as `orderReference`
    # when creating a recovery checkout, so it's the correct field to
    # match a `payment_success` webhook back against
    # FailureEvent.recovery_checkout_order_id. An earlier version of
    # this system matched on transaction_id instead, which was never
    # actually the right field for that purpose.
    order_reference: Optional[str]


def _safe_get(d: dict, *path, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _build_signing_string(payload: dict, timestamp: str) -> str:
    """Builds the exact colon-joined string Nomba signs, per their
    dedicated Webhooks reference doc. Missing fields become empty
    strings — the colon separators are always present, so field
    positions never shift."""
    merchant = _safe_get(payload, "data", "merchant") or {}
    transaction = _safe_get(payload, "data", "transaction") or {}
    fields = [
        payload.get("event_type") or payload.get("event") or "",
        payload.get("requestId") or "",
        merchant.get("userId") or "",
        merchant.get("walletId") or "",
        transaction.get("transactionId") or "",
        transaction.get("type") or "",
        transaction.get("time") or "",
        transaction.get("responseCode") or "",
        timestamp or "",
    ]
    return ":".join(str(f) for f in fields)


def verify_signature(
    payload: dict,
    headers: dict,
    secret: str,
    signature_header: str = "nomba-signature",
    timestamp_header: str = "nomba-timestamp",
) -> None:
    """
    Raises SignatureVerificationError on any failure. Returns nothing —
    a clean return means the signature is valid.

    Must be called with the already-JSON-parsed payload, not raw
    bytes — the signed string is built from specific parsed fields
    plus the nomba-timestamp header value, per Nomba's real scheme
    (see module docstring). This means JSON parsing has to happen
    before verification now, unlike a raw-body HMAC scheme.
    """
    headers = {k.lower(): v for k, v in headers.items()}
    received_signature = headers.get(signature_header.lower())
    timestamp = headers.get(timestamp_header.lower())

    if not received_signature:
        raise SignatureVerificationError("missing signature header")
    if not timestamp:
        raise SignatureVerificationError("missing timestamp header")
    if not secret:
        raise SignatureVerificationError("server misconfigured: no webhook secret set")

    signing_string = _build_signing_string(payload, timestamp)
    digest = hmac.new(
        key=secret.encode("utf-8"),
        msg=signing_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    expected = base64.b64encode(digest).decode("utf-8")

    if not hmac.compare_digest(expected, received_signature):
        raise SignatureVerificationError("signature mismatch")


def extract_event(payload: dict) -> ParsedEvent:
    """Pulls the fields this system needs out of an already-verified,
    already-JSON-parsed payload. Kept separate from verify_signature()
    since verification must happen on raw bytes, while field extraction
    naturally happens on the parsed dict afterwards.

    THREE payload shapes are checked now, in confirmation order:

    1. NESTED, official (developer.nomba.com's actual sandbox-testing
       doc, a real payment_success example):
       data.transaction.merchantTxRef, data.transaction.transactionId,
       data.transaction.transactionAmount, data.order.orderReference,
       data.order.customerEmail, data.order.amount,
       data.order.currency, data.merchant.userId. This is now the
       PRIMARY source — it's an official, current API reference, not
       a training quiz.
    2. FLAT (training.nomba.com's certification quiz — kept as a
       fallback only, since the official doc above supersedes it
       where they disagree): data.merchantTxRef, data.amount,
       data.currency, directly under data.
    3. NESTED, unconfirmed guess (data.transaction.type/time/
       responseCode) — kept since neither confirmed source shows a
       failed-payment example specifically, only payment_success.

    CRITICAL unit-of-currency catch: the official doc's example shows
    `data.order.amount` / `data.transaction.transactionAmount` as
    4000.00 for a checkout ORDER that was created with
    `"amount": "400000.00"` — i.e. these webhook fields are already in
    NAIRA, not kobo. This directly conflicts with the flat shape's
    confirmed `data.amount: 250000` for a ₦2,500 charge, which IS kobo.
    Blindly merging both into one "amount_kobo" field and dividing by
    100 would silently under-report the official-shape amount by 100x.
    They're kept in separate fields (amount_kobo, amount_naira) for
    exactly this reason — see routes/webhooks.py for how they're
    reconciled into a single stored value.
    """
    flat_amount_kobo = _safe_get(payload, "data", "amount")
    nested_amount_kobo_guess = _safe_get(payload, "data", "transaction", "amount")
    confirmed_amount_naira = (
        _safe_get(payload, "data", "order", "amount")
        if _safe_get(payload, "data", "order", "amount") is not None
        else _safe_get(payload, "data", "transaction", "transactionAmount")
    )

    return ParsedEvent(
        event_type=payload.get("event_type") or payload.get("event") or "",
        request_id=payload.get("requestId", ""),
        merchant_tx_ref=(
            _safe_get(payload, "data", "transaction", "merchantTxRef")
            or _safe_get(payload, "data", "merchantTxRef")
        ),
        merchant_user_id=_safe_get(payload, "data", "merchant", "userId"),
        wallet_id=_safe_get(payload, "data", "merchant", "walletId"),
        transaction_id=(
            _safe_get(payload, "data", "transaction", "transactionId")
            or _safe_get(payload, "data", "merchantTxRef")
        ),
        order_reference=_safe_get(payload, "data", "order", "orderReference"),
        transaction_type=_safe_get(payload, "data", "transaction", "type"),
        transaction_time=_safe_get(payload, "data", "transaction", "time"),
        response_code=_safe_get(payload, "data", "transaction", "responseCode"),
        amount_kobo=flat_amount_kobo if flat_amount_kobo is not None else nested_amount_kobo_guess,
        amount_naira=confirmed_amount_naira,
        currency=(
            _safe_get(payload, "data", "order", "currency")
            or _safe_get(payload, "data", "currency")
            or _safe_get(payload, "data", "transaction", "currency")
        ),
        # data.order.customerEmail is now a CONFIRMED real field (not
        # a guess) per the official sandbox-testing doc — checked
        # first. The others remain defensive fallbacks for shapes not
        # explicitly confirmed for a *failed* payment specifically.
        customer_email=(
            _safe_get(payload, "data", "order", "customerEmail")
            or _safe_get(payload, "data", "customerEmail")
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
