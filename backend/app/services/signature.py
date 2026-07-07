"""
Nomba webhook signature verification.

CORRECTION — this file previously treated a training-certification
quiz (training.nomba.com) as the confirmed source for webhook shape.
Nomba's real, current, official developer docs
(developer.nomba.com/docs/products/accept-payment/sandbox-testing)
were located afterward and are now treated as authoritative wherever
the two disagree — an official API reference beats a training quiz.

Confirmed against that official doc's Node.js-equivalent reference:

    const signature = req.header("nomba-signature");
    const expected = crypto
      .createHmac("sha256", process.env.NOMBA_WEBHOOK_SECRET)
      .update(req.body)      // the RAW request body, not a
      .digest("hex");        // reconstructed/reserialized payload

    if (signature !== expected) return res.status(401).send("bad signature");

Still true and unchanged from the earlier version:
- The HMAC is computed over the raw, unparsed request body bytes —
  NOT a colon-joined reconstruction of individual JSON fields. The raw
  body must be captured before any JSON parsing happens, and hashed
  as-is.
- Replay protection is via `event.requestId` idempotency (see
  app/routes/webhooks.py), not a timestamp freshness window.

CORRECTED: the official docs' signature table actually lists FOUR
headers — `nomba-signature`, `nomba-sig-value`,
`nomba-signature-algorithm`, `nomba-timestamp` — not the single header
this file previously claimed was the only one. This is stated here for
accuracy, but doesn't change what this system does: `nomba-signature`
is still the one that matters for HMAC verification, and requestId
idempotency is still used for replay protection rather than the
timestamp header, since requestId dedup already covers the same
problem without needing a freshness-window policy decision.
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
