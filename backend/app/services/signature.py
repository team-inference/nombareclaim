"""
Nomba webhook signature verification.

CRITICAL CORRECTION — everything in this file was previously built
against an assumption that turned out to be wrong in a way that would
have silently rejected every single real Nomba webhook. Nomba's real,
official developer docs (developer.nomba.com/docs/api-basics/webhook)
confirm the actual signature algorithm, with real reference
implementations in six languages (Go, Python, JS, Java, C#, PHP) that
all agree on the same construction:

    hashing_payload = f"{event_type}:{request_id}:{user_id}:{wallet_id}:"
                       f"{transaction_id}:{transaction_type}:{transaction_time}:"
                       f"{transaction_response_code}:{timestamp}"

    signature = base64.b64encode(
        hmac.new(secret.encode(), hashing_payload.encode(), hashlib.sha256).digest()
    ).decode()

Two things this corrects, both load-bearing:

1. The signature is NOT a hash of the raw request body. It's a hash of
   NINE specific fields joined by colons — event_type, requestId,
   merchant.userId, merchant.walletId, transaction.transactionId,
   transaction.type, transaction.time, transaction.responseCode (empty
   string if missing or literally the string "null"), and the
   `nomba-timestamp` HEADER value (not from the payload). An earlier
   version of this file computed HMAC over the raw body — that
   construction produces a completely different signature and would
   reject every genuine webhook as a signature mismatch.

2. The signature is BASE64-encoded, not hex. An earlier version used
   `.hexdigest()`. Even with the correct hashing input, comparing a
   hex string against Nomba's base64 signature would never match.

Practically: this means signature verification cannot happen purely
on raw, unparsed bytes anymore — it requires the same field extraction
that extract_event() already does for business logic, run once,
before any 401 decision is made. This file's verify_signature() now
takes the parsed payload dict and the request headers directly, and
is called with the already-parsed payload in routes/webhooks.py's
webhook handler.

Also corrected: there are FIVE Nomba-specific headers, not one —
`nomba-signature`, `nomba-sig-value` (documented with the same value
in the reference example — kept as a fallback source for the
signature if the primary header is absent), `nomba-signature-algorithm`
(always `HmacSHA256`), `nomba-signature-version` (`1.0.0` currently),
and `nomba-timestamp` (RFC-3339, and — critically — an actual input to
the hash itself, not just informational).

Replay protection remains via `requestId` idempotency (see
routes/webhooks.py), consistent with how Nomba's own docs discuss
duplicate webhook delivery (their retry policy resends the same
`requestId` up to five times on a non-2xx response).
"""
import hashlib
import hmac
import base64
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


def _normalize_response_code(value) -> str:
    """The official reference implementations explicitly check for the
    LITERAL STRING "null" (not just Python/JS null/None) and normalize
    it to an empty string before hashing — a quirk of whatever
    serializes the value on Nomba's side. Mirrored here exactly, since
    getting this wrong means every failure event with no response code
    would fail signature verification."""
    if value is None:
        return ""
    if str(value).lower() == "null":
        return ""
    return str(value)


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

    Takes the PARSED payload (not raw bytes) plus request headers,
    per the confirmed real algorithm — see module docstring. Must be
    called after JSON parsing, unlike the raw-body approach this
    replaced.
    """
    headers = {k.lower(): v for k, v in headers.items()}
    received_signature = headers.get(signature_header.lower()) or headers.get("nomba-sig-value")
    timestamp = headers.get(timestamp_header.lower())

    if not received_signature:
        raise SignatureVerificationError("missing signature header")
    if not timestamp:
        raise SignatureVerificationError("missing nomba-timestamp header")
    if not secret:
        raise SignatureVerificationError("server misconfigured: no webhook secret set")

    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    merchant = data.get("merchant", {}) if isinstance(data, dict) else {}
    transaction = data.get("transaction", {}) if isinstance(data, dict) else {}

    event_type = payload.get("event_type") or payload.get("event") or ""
    request_id = payload.get("requestId", "")
    user_id = merchant.get("userId", "") or ""
    wallet_id = merchant.get("walletId", "") or ""
    transaction_id = transaction.get("transactionId", "") or ""
    transaction_type = transaction.get("type", "") or ""
    transaction_time = transaction.get("time", "") or ""
    transaction_response_code = _normalize_response_code(transaction.get("responseCode"))

    hashing_payload = (
        f"{event_type}:{request_id}:{user_id}:{wallet_id}:"
        f"{transaction_id}:{transaction_type}:{transaction_time}:"
        f"{transaction_response_code}:{timestamp}"
    )

    expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"), hashing_payload.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")

    if not hmac.compare_digest(expected, received_signature):
        raise SignatureVerificationError("signature mismatch")


def extract_event(payload: dict) -> ParsedEvent:
    """Pulls the fields this system needs out of an already-verified,
    already-JSON-parsed payload. verify_signature() above does its own
    minimal, independent extraction of the specific fields the HMAC
    needs (event_type, requestId, merchant.userId/walletId,
    transaction.transactionId/type/time/responseCode) — this function
    extracts the broader set of fields the rest of the business logic
    needs, including the order/customer fields the signature
    calculation doesn't touch at all.

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
