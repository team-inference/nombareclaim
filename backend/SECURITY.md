# NombaReclaim — Architecture & Security Note

Team Inference, DevCareer x Nomba Hackathon 2026.
Backend, AI, and security: Elebiemayo Iseoluwa Emmanuel.

This document covers auth, webhooks, and data handling specifically, as
required by the submission rubric. It is written in plain language on
purpose — every claim here should be checkable by reading the
corresponding file.

## 1. System overview

```
Nomba (webhook) -> POST /webhooks/nomba -> verify signature -> store FailureEvent
                                                              -> background: classify (rules + Gemini/Groq)
Merchant dashboard -> GET /api/summary, /api/failures -> read from FailureEvent table
                    -> POST /api/failures/{id}/trigger-recovery -> Nomba Checkout API -> recovery link
Nomba (webhook, payment_success on the recovery checkout) -> server-side transaction lookup -> mark RECOVERED
```

Two webhook directions exist in this system: inbound failure/abandonment
events that create FailureEvent rows, and inbound success events that
*may* close the loop on a recovery checkout we created. Both pass
through the same signature verification path before anything else
happens.

**Deployment**: the backend runs on Railway (root directory `/backend`,
Python auto-detected via `requirements.txt`, start command from the
included `Procfile`). All secrets are set as Railway environment
variables, never committed — see section 6.

## 2. HMAC signature verification

Every inbound request to `/webhooks/nomba` is verified before any
business logic runs, in `app/services/signature.py`.

**This section is now confirmed against Nomba's own official training
documentation** (training.nomba.com > Webhooks module), not inferred —
an earlier draft of this system had to guess at parts of this scheme;
that guess has since been replaced with their documented reference
implementation, matched exactly.

- **Algorithm**: HMAC-SHA256, hex-encoded.
- **What's hashed**: the raw, unparsed request body bytes — exactly as
  received, before any JSON parsing happens. This matters: hashing a
  re-serialized version of the parsed JSON can produce a different
  byte sequence than what was actually sent (whitespace, key
  ordering), which would make a genuinely valid signature look
  invalid. The raw body is captured first and signature verification
  runs against those exact bytes; JSON parsing only happens afterward,
  once the signature has already passed.
- **Where the signature lives**: a single request header,
  `nomba-signature`, compared against the locally-computed digest.
  Nomba's own sample code confirms this is the only header involved —
  there is no separate timestamp header in their scheme.
- **The secret**: referred to in this codebase as
  `NOMBA_WEBHOOK_SIGNATURE_KEY`, matching what Nomba calls
  `NOMBA_WEBHOOK_SECRET` in their own sample — the secret Nomba
  generates when you register a webhook URL on their dashboard.
- **Comparison**: `hmac.compare_digest`, never `==`, to avoid leaking
  timing information about how many bytes of a forged signature were
  correct.
- **Failure mode**: any verification failure (bad signature, missing
  header) returns `401` immediately. The response body never echoes
  the computed or expected signature, the signing key, or *why
  specifically* verification failed beyond a generic reason — an
  attacker probing the endpoint learns nothing useful from a failed
  attempt.

## 3. Replay protection

Nomba's own webhook documentation does not implement timestamp-based
replay protection — their reference sample has no timestamp header at
all. Instead, they explicitly recommend idempotency on
`event.requestId`: ignore an event if that requestId has already been
processed. This system implements exactly that (see section 4) as its
replay defense, rather than a freshness-window check that Nomba's own
scheme doesn't support. A captured, validly-signed request replayed
later is caught here, not by a timestamp — the second delivery carries
the same `requestId` and is detected and ignored before any
reprocessing happens.

## 4. Idempotency

Enforced at two separate points, both required by the rubric and both
covered by tests:

1. **Webhook ingestion** (`app/routes/webhooks.py`): an `idempotency_key`
   is derived as `{event_type}:{transaction_id}:{request_id}` and
   stored uniquely on `FailureEvent`. A redelivery of the same event
   (Nomba retries on any non-2xx response, per their backoff policy)
   is detected before any row is created and returns `200` immediately
   without reprocessing. See `tests/test_webhooks.py::test_duplicate_delivery_is_not_reprocessed`.
2. **Recovery triggering** (`app/services/recovery.py`): if a
   `FailureEvent` is already `RECOVERY_TRIGGERED` or `RECOVERED`, a
   repeat call to the trigger endpoint returns the existing state
   rather than calling Nomba's checkout API again. This protects
   against both a merchant double-clicking the dashboard button and
   any retried automated trigger.

## 5. Why recovery status is only ever finalized server-side

The system never marks a `FailureEvent` as `RECOVERED` purely because a
`payment_success` webhook arrived. When that webhook arrives, it is
matched against a known `recovery_checkout_order_id`, and then — before
anything is written — `nomba_client.get_transaction_status(...)` is
called to independently ask Nomba's API whether that transaction
actually succeeded. Only that server-side answer can flip the status
(`app/services/recovery.py::confirm_recovery_if_paid`). A forged or
replayed `payment_success` event, even a perfectly-signed one for a
transaction reference that doesn't actually exist or hasn't actually
settled, cannot move money or move dashboard state on its own — it can
only trigger a lookup.

## 6. Secret handling

- All secrets (`NOMBA_CLIENT_ID`, `NOMBA_PRIVATE_KEY`,
  `NOMBA_WEBHOOK_SIGNATURE_KEY`, `GEMINI_API_KEY`) are read from
  environment variables via `app/config.py`. None are hardcoded
  anywhere in the codebase.
- `.env` is in `.gitignore` from the repository's first commit. Only
  `.env.example`, with empty values, is committed.
- In production, the same variables are set directly in Railway's
  environment variable dashboard, never in a committed file.
- **TEST credentials only** are used for all development, the July 3
  checkpoint, and the demo recording. There is no live-money path
  exercised anywhere in this build.
- If there is ever doubt that a live private key was exposed (for
  example, pasted into a chat or document), it is rotated from the
  Nomba dashboard before being trusted again — credentials, once
  potentially seen by an unintended party, are treated as compromised
  rather than "probably fine."

## 7. accountId / sub-account scoping

Every authenticated call to Nomba's API carries two distinct identifiers
that must not be confused:

- The **parent `accountId`** goes in the request header (`accountId:
  <parent>`) on every call, including the token-issue call itself —
  this is what Nomba's auth and most resource endpoints expect.
- The **sub-account id** is used inside specific request *bodies* where
  Nomba's API asks which account a resource (like a checkout order) is
  scoped to — for example, the `accountId` field inside the `order`
  object when creating a checkout order is the sub-account, not the
  parent.

Getting this backwards is a common and easy mistake (the field name
`accountId` appears in both places, meaning two different things). This
is documented explicitly in `app/services/nomba_client.py` next to each
use, not left implicit.

## 8. Rate limiting

`POST /api/failures/{id}/trigger-recovery` is rate-limited per
client-IP-and-event-id pair (`app/middleware/rate_limit.py`): a sliding
window allowing `RATE_LIMIT_MAX_REQUESTS` (default 5) requests per
`RATE_LIMIT_WINDOW_SECONDS` (default 60). This is intentionally simple
in-memory state, appropriate for a single-instance hackathon deployment
— it is **not** distributed and resets on every restart/redeploy. That
trade-off is acceptable at this scale and is stated here rather than
implied to be production-grade.

## 9. Data handling

- The full raw webhook payload is stored (`FailureEvent.raw_payload`)
  for debugging and auditability, not just the extracted fields. No
  card numbers, CVVs, or full PANs ever pass through this system — the
  failure/abandonment webhook payloads contain transaction metadata
  (amounts, response codes, wallet/transaction IDs), not raw card data,
  consistent with Nomba's own PCI scope sitting at their layer, not
  this merchant-side service's.
- Customer-facing recovery messages avoid restating sensitive failure
  detail (e.g. no raw bank decline codes shown to the customer — the
  message is a friendly, generic nudge, with the technical
  classification kept on the merchant-facing dashboard only).

## 10. What is deterministic vs. AI-generated (for judges who ask)

- **Classification** (`app/services/classification.py`) is deterministic
  wherever Nomba's response code maps unambiguously to a failure
  reason (a dict lookup against `RESPONSE_CODE_MAP`). AI is called
  only to classify the genuinely ambiguous remainder.
- **Recovery score** is a deterministic, explainable function of
  classification type plus a mild amount-based adjustment — not a
  trained model and not opaque.
- **Recovery message text** is always AI-generated (never templated
  for the live demo unless every AI call fails, in which case a
  clearly-labeled fallback template is used so the pipeline degrades
  gracefully instead of breaking).
- **AI provider chain**: Gemini is tried first; Groq is tried only if
  Gemini is unconfigured or its call fails for any reason; a plain
  deterministic template is the final fallback if both AI providers
  are unavailable. Every provider call catches its own exceptions and
  returns nothing rather than raising, so a third-party AI outage can
  never break webhook ingestion — worst case, the message is a
  template instead of AI-written, but the failure event is still
  captured, classified by rules, and shown on the dashboard.

## 11. Honest scope statement

What this build does **not** do, stated upfront rather than discovered
by a judge: no fully-automatic trigger-on-classification path exists
(triggering recovery is currently a deliberate human action from the
dashboard, gated only by simple rate limiting); no "payday timed retry"
logic is implemented (flagged in the team's own strategy notes as the
riskiest line in the original pitch, and intentionally cut rather than
faked); dashboard updates after a recovery completes via manual refresh,
not push/websockets. None of this is hidden in the demo.
