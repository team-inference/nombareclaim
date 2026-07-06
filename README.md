# NombaReclaim

**Turning failed payments into recovered revenue.**

An intelligent failed-payment recovery engine for Nomba merchants. It intercepts webhook failure events in real time, uses AI to classify *why* each payment failed, and automatically generates a personalized recovery checkout link to win the customer back — surfaced on a live merchant dashboard.

Built for the **DevCareer x Nomba Hackathon 2026** by **Team Inference**.

- **Elebiemayo Iseoluwa Emmanuel** — Full-Stack Developer & AI (frontend + backend)
- **Aborowa Daniel Toluwanimi** — Lead, Product & Security

## Live

- **Dashboard:** https://nombareclaim.vercel.app
- **API:** https://nombareclaim-production.up.railway.app
- **API health check:** https://nombareclaim-production.up.railway.app/health

## What it does

1. **Captures failures in real time** — a signed Nomba webhook fires the instant a payment fails, verified with HMAC-SHA256 before anything is trusted, and deduplicated by `requestId` so retried deliveries never double-process.
2. **Classifies the failure with AI** — Gemini (primary) or Groq (fallback) reads the failure context and labels *why* it likely failed — insufficient funds, expired card, 3-D Secure drop-off, etc. — with a deterministic rule-based classifier as a last-resort fallback, so ingestion never blocks on an AI provider being down.
3. **Generates a personalized recovery link** — a fresh Nomba Checkout session is created for the customer, with a recovery message tailored to the classified failure reason.
4. **Recovers automatically, not just manually** — when a webhook carries a customer email and the AI's recovery-confidence score clears a configurable threshold, NombaReclaim emails the customer a recovery link on its own — no merchant click required. This is opt-in and off by default (`RECOVERY_AUTOMATION_ENABLED`), so a fresh deployment never silently emails a real customer until someone turns it on.
5. **Follows up with "payday retry"** — if the first email doesn't convert, a background scheduler follows up automatically: insufficient-funds failures are retried around Nigeria's common salary-payment window (the 25th–1st), while every other failure type gets a short fixed backoff (3h / 24h / 72h). Stops after a configurable number of attempts so no customer is emailed forever.
6. **Confirms recovery server-side only** — a `payment_success` webhook alone can never mark a transaction recovered; it's always cross-checked against Nomba's own order-status endpoint first, so a forged or replayed webhook can't move dashboard state on its own.
7. **Surfaces it all on a merchant dashboard** — live failure feed, real (not fixture) recovery-rate trend, a breakdown of recovery performance by failure reason, per-transaction recovery/retry status, and a one-click CSV export for a merchant's own reporting.

## Tech stack

| Layer | Stack |
|---|---|
| Backend | FastAPI, SQLAlchemy, httpx, deployed on Railway |
| Frontend | React, Vite, Tailwind CSS, Recharts, deployed on Vercel |
| AI | Gemini (primary), Groq (fallback), rule-based fallback (last resort) |
| Payments | Nomba Checkout API, HMAC-SHA256 signed webhooks |
| Notifications | SMTP (stdlib `smtplib` — works with any provider, no SDK dependency) |
| Scheduling | In-process asyncio retry sweep — appropriate for this single-instance deployment |
| Testing | Pytest (backend), 32 automated tests covering signature verification, idempotency, auto-recovery, retry scheduling, and analytics |

## Structure

```
/backend   FastAPI service — webhook receiver, AI classification engine, Nomba API integration,
           automated recovery + payday retry scheduler
/frontend  React dashboard — failure feed, recovery-rate trend, failure-reason breakdown, CSV export
```

## Running locally

**Backend**
```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your Nomba TEST credentials, sub-account ID, and AI keys
uvicorn app.main:app --reload
```
Backend runs at `http://localhost:8000`. See `.env.example` for every required variable — Nomba credentials, webhook signing secret, AI provider keys, and rate-limit/recovery-score tuning.

**Frontend**
```bash
cd frontend
npm install
cp .env.example .env   # set VITE_API_BASE_URL to your backend URL
npm run dev
```
Frontend runs at `http://localhost:5173`. Set `VITE_USE_MOCKS=true` in `.env` to explore the UI with sample data without a running backend.

## Security

See [`/backend/SECURITY.md`](./backend/SECURITY.md) for the full architecture and security write-up — HMAC-SHA256 webhook verification, idempotent event processing, sub-account scoping, automated recovery/retry design, and secret handling — as required by the hackathon submission rubric.

## Status

This is the **Final Submission** for Demo Day. Webhook ingestion, AI classification, checkout generation, automated recovery emails, payday-retry scheduling, and the live dashboard (including real trend and failure-reason breakdown data) are all working end-to-end against Nomba's sandbox environment, backed by 32 automated tests. Honestly stated, not hidden: automated recovery currently only reaches customers by email (no SMS/WhatsApp channel yet, though the data model is ready for one); the retry scheduler is a single-instance in-process loop, appropriate for this deployment but not horizontally distributed; and dashboard updates after a recovery completes via manual refresh, not push/websockets. See `SECURITY.md` section 15 for the complete honest-scope statement.
