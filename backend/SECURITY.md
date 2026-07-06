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
                                                              -> background: maybe_auto_recover (opt-in, score-gated)
                                                                 -> Nomba Checkout API -> recovery email (SMTP)
                                                                 -> schedules next_retry_at (payday / backoff)
Background retry sweep (opt-in) -> due FailureEvents -> fresh checkout link -> recovery email -> reschedule
Merchant dashboard -> GET /api/summary, /api/summary/trend, /api/analytics/breakdown, /api/failures, /api/export
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

   **Event type matching is case-insensitive on purpose.** Nomba's real
   API docs (event-log endpoint reference) use uppercase values —
   `PAYMENT_SUCCESS`, `PAYMENT_FAILED` — while a separate training-site
   example payload used lowercase under a different field name
   (`"event":"payment_success"`). Neither source is confirmed as the
   literal shape of a production webhook body, so `event_type` is read
   from either `event_type` or `event`, and matched against
   `FAILURE_EVENT_TYPES`/`SUCCESS_EVENT_TYPES` case-insensitively,
   rather than betting the whole ingestion pipeline on one guess. This
   is stated here rather than silently handled, and should be
   simplified back to an exact match once a real webhook delivery
   confirms the true casing.
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

Per Nomba's own credentials brief for this hackathon ("Authenticate
with the parent Account ID in the `accountId` header, then scope your
calls to your sub-account ID."), this system uses two different
`accountId` values depending on the call:

- **Token issuance** (`_get_access_token` in `app/services/nomba_client.py`):
  the **parent** `accountId` (`NOMBA_ACCOUNT_ID`).
- **Every call made after auth** — checkout order creation, order
  status lookup (`_auth_headers`): the **sub-account** `accountId`
  (`NOMBA_SUBACCOUNT_ID`).

**Correction from an earlier draft**: `NOMBA_SUBACCOUNT_ID` was defined
in `config.py` from early on but was never actually referenced
anywhere — every post-auth call was sending the parent account ID
instead, which directly contradicted the credentials brief above. This
was caught and fixed by wiring `_auth_headers()` to use
`NOMBA_SUBACCOUNT_ID`, leaving token issuance on the parent ID exactly
as specified.

Still genuinely open: whether the checkout order **request body**
(not just the header) also needs an explicit sub-account field nested
inside `order` — the confirmed training example's body doesn't include
one, and the credentials brief only speaks to the header. This client
does not add one to the body, matching the confirmed training example,
while scoping the header correctly per the brief. Worth confirming
against a real sandbox transaction if checkout orders ever appear
under the wrong sub-account in Nomba's own reporting.

## 7b. Confirmed API details (from Nomba's official training material)

The following were corrected after reviewing Nomba's own developer
certification training material directly (not third-party pages),
replacing earlier guesses that turned out to be wrong:

- **Sandbox and production are separate hosts**, not the same host
  with different credentials:
  - Sandbox (all hackathon work): `https://sandbox.api.nomba.com/v1`
  - Production (post-KYC only): `https://api.nomba.com/v1`
  An earlier draft of `app/config.py` assumed a single shared host —
  that was wrong and has been corrected.
- **Amounts are in kobo**, as integers, everywhere in Nomba's API —
  confirmed by their own example (`amount: 250000` for a ₦2,500.00
  charge). This system stores and displays amounts in naira
  throughout (matching the shared dashboard API contract), so the
  kobo↔naira conversion happens at exactly one boundary —
  `app/services/nomba_client.py`'s `_naira_to_kobo`/`_kobo_to_naira`
  helpers — and nowhere else in the codebase needs to think about
  kobo. An earlier draft sent a decimal-string naira amount directly
  to Nomba's API, which would have been a real ~100x financial
  correctness bug had it gone uncaught before a real transaction.
- **Checkout response field is `checkoutUrl`**, not `checkoutLink` —
  an earlier draft had this field name wrong, which would have caused
  every recovery-checkout creation to fail with a "missing checkoutUrl"
  error against the real API despite looking correct against no live
  credentials to test with.
- **A confirmed direct status endpoint exists**:
  `GET /checkout/order/{orderReference}`. This replaced an earlier,
  unconfirmed guess that queried a `/transactions/accounts` list
  endpoint and filtered results client-side.
- **Webhook payload shape**: Nomba's own confirmed example for a
  `payment_success` event uses a flat structure —
  `data.merchantTxRef`, `data.amount`, `data.currency` directly under
  `data` — not the deeply nested `data.transaction.transactionId`
  structure an earlier draft assumed. `services/signature.py`'s
  `extract_event()` now tries the confirmed flat shape first, falling
  back to the nested guess only if the flat fields aren't present,
  since no confirmed shape exists yet specifically for a *failed*
  payment event (only the success-side example was shown).
- **Still genuinely open, not resolved by any source seen so far**:
  whether Nomba's actual failed-payment webhook event is literally
  named `PAYMENT_FAILED` — the "common event types" list in the
  training material does not include a failure event at all (only
  `payment_success`, `virtual_account.funded`, `transfer.success`,
  `transfer.failed`, `mandate.debit_success`), while a different
  endpoint's reference (event-log/replay filter values) does list
  `PAYMENT_FAILED` as valid. The authoritative source — the literal
  event name shown in Nomba's real dashboard when registering a
  webhook and selecting which events to receive — should be checked
  directly before the demo. Nomba's sandbox also ships real test
  instruments for exactly this purpose: a documented "insufficient
  funds" test card (`5060 6666 6666 6666 674`) that can trigger a
  genuine sandbox failure and remove all doubt about the real payload
  shape, rather than continuing to reason from documentation alone.

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

## 11. Automated recovery (email) — opt-in, hard-gated

Beyond the manual "trigger recovery" dashboard button, this build adds
a fully-automatic path: `app/services/recovery.py::maybe_auto_recover`,
called from the classification background task
(`app/routes/webhooks.py::_run_classification`) immediately after a
new failure event is classified.

**This is off by default** (`RECOVERY_AUTOMATION_ENABLED=false`) — a
fresh deployment should never silently email a real customer until
someone deliberately turns it on. When enabled, it only fires when
*all* of the following hold:

1. The webhook payload actually contained a customer email
   (`customer_email` on `FailureEvent`) — opportunistically extracted
   in `services/signature.py` from several possible field names/paths,
   since no confirmed Nomba example payload includes this field at
   all. Absent it, automation simply never fires for that event —
   recovery still works, just manually via the dashboard link.
2. `recovery_score` clears `AUTO_RECOVERY_MIN_SCORE` (default 40) —
   unlike the manual dashboard button, which has no score gate at all
   and lets a merchant/judge override the AI's confidence.
3. The event hasn't already had recovery triggered.

When it fires: a checkout link is generated exactly like the manual
path, then a plain-text email is sent via `services/notifications.py`
(stdlib `smtplib`, works with a Gmail app password or any SMTP relay —
no new dependency added). If SMTP isn't configured
(`SMTP_HOST`/`SMTP_USERNAME`/`SMTP_PASSWORD`/`SMTP_FROM_EMAIL` all
blank by default), `send_recovery_email` logs and returns `False`
rather than raising — the rest of the pipeline (classification,
checkout link generation, dashboard) keeps working with automation
just never actually sending anything.

## 12. Payday retry — automatic follow-up scheduling

If the first automated email is sent, `services/scheduling.py`
computes `next_retry_at` for a follow-up attempt:

- **`INSUFFICIENT_FUNDS`**: scheduled around Nigeria's common
  salary-payment window (`PAYDAY_RETRY_DAYS`, default the 25th through
  end of month plus the 1st) rather than a short fixed delay — an
  empty wallet is far more likely to succeed once the customer has
  actually been paid than it is three hours later.
- **Every other classification**: a short fixed backoff
  (`RETRY_BACKOFF_HOURS`, default 3h / 24h / 72h) — these failures
  (card declined, network timeout, abandoned) aren't tied to a
  predictable future event the way an empty wallet is.
- Both stop after `MAX_AUTO_RETRIES` (default 3) — a customer is never
  emailed indefinitely.

A background asyncio loop (`services/scheduler.py::retry_sweep_loop`),
started at app startup only when `RECOVERY_AUTOMATION_ENABLED=true`,
wakes every `RETRY_SWEEP_INTERVAL_SECONDS` (default 300s) and hands
every due `FailureEvent` to `services/recovery.py::send_retry_recovery`,
which generates a **fresh** checkout order (the first one may well
have expired) with a unique order reference per attempt
(`reclaim-{id}-r{n}`), re-sends the email, and reschedules the next
attempt if any remain.

This is deliberately a single in-process asyncio loop, not
APScheduler/Celery/a separate cron service — appropriate for a
single-instance Railway deployment with one worker process. **If this
ever runs across multiple instances, it needs to move to a real job
queue so two workers can't double-send the same retry** — stated here
rather than silently assumed away.

## 13. Analytics & export

Two read-only additions, both consistent with the existing
public/unauthenticated dashboard API (see section 9 on data handling —
no customer PII in either):

- `GET /api/analytics/breakdown` — recovery performance grouped by
  AI-classified failure reason (count, amount at risk, recovered
  count/amount, recovery rate per classification). Powers the
  dashboard's "Recovery by Failure Reason" chart.
- `GET /api/export` — CSV export of every captured failure event
  (transaction id, amount, classification, status, `has_contact`
  boolean, retry count, timestamps) for merchants who want this in
  their own spreadsheet/BI tool.

Also fixed in this pass: `GET /api/summary/trend` **did not previously
exist** — the frontend's `getRecoveryTrend()` silently fell back to
fixture data on a 404, which is why the dashboard's 7-day trend chart
kept showing a plausible-looking curve even on a fresh deployment with
zero real failure events everywhere else. It's now a real endpoint
returning cumulative recovery rate per day, computed from actual
`FailureEvent` rows.

## 14. Configurable webhook event type names

`NOMBA_FAILURE_EVENT_TYPES` / `NOMBA_SUCCESS_EVENT_TYPES` (both env
vars, comma-separated, matched case-insensitively) replace what were
previously hardcoded constants in `routes/webhooks.py`. This exists
specifically because the real event name for a failed payment is
still not confirmed by any doc source seen so far (see section 7b) —
if it turns out to differ from `PAYMENT_FAILED` once confirmed against
Nomba's dashboard or a live sandbox test, that's now a one-line env var
change on Railway, not a code change and redeploy.

## 15. Honest scope statement

What this build does **not** do, stated upfront rather than discovered
by a judge: dashboard updates after a recovery completes via manual
refresh, not push/websockets; the automated retry sweep is a
single-instance in-process loop, not a distributed job queue (see
section 12); SMS/WhatsApp recovery channels are not implemented, only
email, since only `customer_email` is opportunistically extracted from
the webhook payload today, not phone number, even though the
`customer_phone` field exists on the model for a future channel; and
the checkout order body's sub-account scoping (as opposed to the
header, which is fixed) remains genuinely unconfirmed (see section 7).
None of this is hidden in the demo.
