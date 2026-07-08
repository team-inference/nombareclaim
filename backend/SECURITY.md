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

**CRITICAL CORRECTION, discovered during live debugging on the final
submission day**: this section previously described an algorithm
confirmed against Nomba's training-certification quiz
(training.nomba.com). That algorithm was wrong in a way that would
have silently rejected every single real Nomba webhook as a signature
mismatch, disguised as an ordinary-looking `401`. It was caught before
causing real damage only because reachability was independently
verified first (a manual test webhook correctly returned `401` for a
*missing* signature — which looked like confirmation the system worked,
but never actually exercised the *matching* logic against a real
signature). Nomba's real, official developer docs
(developer.nomba.com/docs/api-basics/webhook) — which include full
reference implementations in Go, Python, JavaScript, Java, C#, and
PHP, all agreeing with each other — reveal the actual algorithm:

- **Algorithm**: HMAC-SHA256, **base64-encoded** (not hex).
- **What's hashed**: NOT the raw request body. A colon-joined string
  of nine specific fields:
  ```
  {event_type}:{requestId}:{merchant.userId}:{merchant.walletId}:
  {transaction.transactionId}:{transaction.type}:{transaction.time}:
  {transaction.responseCode}:{nomba-timestamp header value}
  ```
  `transaction.responseCode` is normalized to an empty string if it's
  missing or literally the string `"null"` (a quirk of Nomba's own
  serialization, mirrored exactly — see
  `signature.py::_normalize_response_code`). Because the hash depends
  on parsed fields rather than raw bytes, JSON parsing must now happen
  **before** signature verification, the reverse of the previous
  order.
- **Where the signature lives**: `nomba-signature` (falls back to
  `nomba-sig-value`, shown with an identical example value in the
  official docs). There are actually FIVE Nomba-specific headers, not
  one: the two above, plus `nomba-signature-algorithm`
  (`HmacSHA256`), `nomba-signature-version` (`1.0.0`), and
  `nomba-timestamp` — which is not just informational, it's a literal
  **input to the hash**, read via the (previously unused) env var
  `NOMBA_TIMESTAMP_HEADER`.
- **The secret**: `NOMBA_WEBHOOK_SIGNATURE_KEY`, the secret Nomba
  generates when a webhook URL is registered.
- **Comparison**: `hmac.compare_digest`, never `==`, to avoid leaking
  timing information about how many bytes of a forged signature were
  correct.
- **Failure mode**: any verification failure (bad signature, missing
  signature header, missing timestamp header) returns `401`
  immediately. The response body never echoes the computed or
  expected signature, the signing key, or *why specifically*
  verification failed beyond a generic reason.

This is documented in detail, including a full citation and worked
example, in `services/signature.py`'s module docstring —
`test_signature.py::test_signature_matches_nomba_documented_scheme_exactly`
proves this implementation produces byte-for-byte the same signature
as an independent reference built directly from Nomba's own published
sample code, not copied from this file.

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

## 7b. Confirmed API details — CORRECTED against Nomba's real official docs

**Important update**: everything in this section was originally
written against Nomba's training-certification material
(training.nomba.com, a quiz-style onboarding course). Nomba's real,
current, official developer docs (developer.nomba.com) were located
afterward, during live debugging when the sandbox host turned out not
to resolve via DNS at all. Wherever the two disagree, the official
docs are now treated as authoritative — a live API reference beats a
training quiz, however official-sounding the quiz seemed at the time.
Several things below were themselves wrong as a result and are now
corrected a second time:

- **The sandbox hostname itself was wrong.** `https://sandbox.api.nomba.com/v1`
  (from the training quiz) is not a real domain — confirmed
  non-resolving against two independent DNS resolvers (a Railway
  deployment's own DNS, and Google's 8.8.8.8) during live debugging.
  The real sandbox host, per the official docs, is
  `https://sandbox.nomba.com` — no `api.` subdomain. Production is
  unaffected: `https://api.nomba.com`.
- **Sandbox checkout endpoints live under a different path prefix
  entirely** — `/sandbox/checkout/...`, not `/v1/checkout/...`. This
  isn't just a different host; it's a genuinely different path
  structure for checkout-specific operations. Auth
  (`/v1/auth/token/issue`) is NOT affected — same `/v1` prefix in both
  environments. `app/services/nomba_client.py` now branches on
  whether `NOMBA_API_BASE_URL` contains "sandbox" to pick the right
  prefix (`_checkout_path_prefix()`).
- **Checkout response field is `checkoutLink`, not `checkoutUrl`** —
  the training quiz's claim that `checkoutUrl` was confirmed was
  itself wrong. The official doc's real example response shows
  `data.checkoutLink`. Both are now accepted defensively
  (`checkoutLink` preferred), in case a real response ever differs
  from this specific example.
- **The transaction-verification endpoint is different than
  previously coded.** Previously: `GET /checkout/order/{orderReference}`
  (an unconfirmed guess, never actually verified against a real
  response). Actually, per the official doc:
  `GET /sandbox/checkout/transaction?idType=orderReference&id={ref}`
  in sandbox. Response shape is also now confirmed: `data.success`
  (boolean) and `data.message` / `data.transactionDetails.statusCode`
  (text, observed value `"PAYMENT SUCCESSFUL"`) — `services/recovery.py`'s
  `confirm_recovery_if_paid` now checks these as the primary signal,
  with the earlier guessed field names kept only as a fallback.
- **Webhook payload shape is nested, with a confirmed `customerEmail`
  field** — `data.transaction.merchantTxRef`,
  `data.transaction.transactionId`, `data.order.orderReference`,
  `data.order.customerEmail`, `data.merchant.userId`. This directly
  contradicts the earlier belief that the training quiz's FLAT shape
  (`data.merchantTxRef`, `data.amount` directly under `data`) was the
  confirmed one — that flat shape is now kept only as a fallback for
  payloads that don't match the official nested shape.
  **Practically important**: `data.order.customerEmail` being a
  confirmed real field (not a defensive guess) means the automated
  recovery email pipeline (section 11) has a much better chance of
  actually having a real address to send to than previously assumed.
- **A genuine, still-unresolved conflict on amount units between the
  two doc sources.** The training quiz's flat shape
  (`data.amount: 250000` for ₦2,500.00) is in KOBO. The official
  doc's nested shape (`data.order.amount` / `data.transaction.transactionAmount`,
  both showing `4000.00` for a checkout order that was created with
  `"amount": "400000.00"`) is in NAIRA — i.e. already the actual
  currency unit, not kobo. Blindly merging both into one field and
  dividing by 100 would have silently under-reported the
  officially-shaped amount by 100x. `services/signature.py`'s
  `ParsedEvent` now keeps `amount_kobo` and `amount_naira` as
  separate fields for exactly this reason; `routes/webhooks.py`'s
  `_resolve_amount_naira()` prefers the confirmed-naira value when
  present, falling back to the kobo conversion only when it's absent.
- **RESOLVED: checkout REQUEST body amount format.** The official
  sandbox-testing doc's checkout-creation example sends
  `"amount": "400000.00"` — a decimal STRING. An earlier version of
  this document called this "a genuine, unresolved conflict" against
  the training quiz's `amount: 250000` (a plain integer) and left the
  request sending a bare integer without changing it. That framing
  was overstated: the training quiz's example is from a WEBHOOK
  PAYLOAD (something Nomba sends us), while the sandbox-testing
  example is from a CHECKOUT ORDER CREATION REQUEST (something we
  send to Nomba) — two different API surfaces, not necessarily the
  same wire format. `_naira_to_kobo()` now sends the decimal-string
  format, matching the only confirmed real example for this specific
  endpoint's request body.
- **Recovery confirmation now matches on the correct field.**
  `data.order.orderReference` is confirmed to be exactly the value
  this system itself sets as `orderReference` when creating a
  recovery checkout (see `services/recovery.py`). An earlier version
  matched an incoming `payment_success` webhook against
  `transaction_id` instead, which was never actually the right field
  for this purpose — kept as a fallback only for payloads without a
  separate `order` object.
- **RESOLVED**: the real event name for a failed payment is confirmed
  as `payment_failed` (lowercase, underscore) — found in a second
  official doc page (developer.nomba.com/docs/api-basics/webhook),
  distinct from the sandbox-testing page that only showed a
  `payment_success` example. The confirmed example is a POS-originated
  purchase (`data.transaction.responseCodeMessage: "Insufficient
  Funds"`, `responseCode: "51"`) rather than an online-checkout
  failure specifically, so it doesn't confirm whether `data.order.*`
  (including `customerEmail`) is present on a failed *checkout*
  specifically the way it's confirmed present on a successful one —
  that narrower point is the one thing about payload shape still
  worth checking against a real sandbox test, not the event name
  itself, which is settled. Matching in `NOMBA_FAILURE_EVENT_TYPES`
  already handled this correctly via case-insensitive matching before
  this was even confirmed, since the default value `PAYMENT_FAILED`
  normalizes to the same uppercase form either way.
- Also corrected: the earlier claim that there is exactly ONE webhook
  header and no timestamp header at all was itself wrong — the
  official doc's signature section lists FOUR headers
  (`nomba-signature`, `nomba-sig-value`, `nomba-signature-algorithm`,
  `nomba-timestamp`). This doesn't change what the system does —
  `nomba-signature` is still the one used for HMAC verification, and
  `requestId` idempotency is still used for replay protection rather
  than the timestamp header — but the earlier claim of "exactly one
  header" was factually wrong and is corrected here for accuracy.
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

## 16. How the training-quiz-vs-official-docs discrepancy was found

Worth recording plainly, since it materially changed several
"confirmed" claims in this document: for most of the build, this
system was validated against Nomba's training-certification quiz
(training.nomba.com), including its stated sandbox host
`https://sandbox.api.nomba.com/v1`. That host never actually worked —
confirmed via `nslookup` against two independent DNS resolvers (a
local resolver and Google's `8.8.8.8`), both returning NXDOMAIN, during
live debugging of why the deployed dashboard showed zero data despite
credentials being correctly configured on Railway.

Searching for the real sandbox host surfaced Nomba's actual, current,
official developer documentation at `developer.nomba.com` — a
genuinely different site than the training quiz, with its own API
reference, sandbox-testing guide, and real request/response examples.
Cross-checking against it surfaced every correction listed in section
7b above, several of which (`checkoutLink` vs `checkoutUrl`, the
transaction-verification endpoint, the webhook payload shape) would
otherwise have caused real, hard-to-diagnose failures the first time
this system tried to talk to Nomba's actual sandbox rather than a
non-existent host.

The lesson generalized: a training/certification quiz and a live API
reference are not the same category of source, even when both claim
official status, and a live DNS/connectivity failure is worth treating
as a signal to re-verify assumptions rather than just a networking
inconvenience to work around.

## 17. A second, more serious correction: the signature algorithm itself

Everything in section 16 concerned the sandbox host and checkout API
shape. A second, more serious problem was found afterward, in the same
final debugging session: the HMAC signature algorithm this system
implemented — hashing the raw request body, hex-encoded — was
confirmed wrong against `developer.nomba.com/docs/api-basics/webhook`'s
actual reference implementations. The real algorithm hashes nine
specific parsed fields (not the raw body) and base64-encodes the
result (not hex) — see section 2 for the corrected algorithm in full.

This is a materially more dangerous class of bug than the sandbox-host
typo: a wrong hostname fails loudly and immediately (DNS resolution
error, impossible to miss). A wrong signature algorithm fails
*quietly* — every legitimate webhook would have been rejected with an
ordinary-looking `401 signature mismatch`, indistinguishable in the
logs from an actual forged request, and easy to misread as "Nomba
still isn't sending us anything" rather than "we're rejecting
everything they send." It was caught only because an unsigned test
request (confirming the endpoint was reachable and returned 401 for a
*missing* signature) was mistaken for stronger evidence than it
actually was — that test never exercised the signature-matching logic
at all. The general lesson: confirm a security check rejects bad input
for the *right* reason, not just that it rejects *something*.

## 18. Self-service webhook diagnostics (Nomba's REST API)

Nomba exposes a REST API for inspecting and replaying webhook delivery
history directly — `developer.nomba.com/docs/api-basics/
troubleshoot-webhooks` — which is a faster diagnostic path than
waiting on hackathon support for "did you actually send us anything":

- **List delivered webhook events** for an account/date range/event
  type, to see directly whether Nomba attempted delivery at all,
  independent of anything in this system's own logs.
- **Re-push a specific event** by ID, or **replay a whole date range**,
  to manually trigger redelivery of a real event to the registered
  URL — useful for testing the full pipeline end-to-end without
  waiting for a new failure to occur naturally.

This wasn't wired into NombaReclaim itself (it's a diagnostic tool for
developers, not something a merchant-facing recovery engine needs to
call), but it's the correct next step for confirming, independent of
any Slack conversation, whether webhook forwarding is actually active
for this project's sub-account.
