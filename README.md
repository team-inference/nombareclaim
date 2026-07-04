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
4. **Confirms recovery server-side only** — a `payment_success` webhook alone can never mark a transaction recovered; it's always cross-checked against Nomba's own order-status endpoint first, so a forged or replayed webhook can't move dashboard state on its own.
5. **Surfaces it all on a merchant dashboard** — live failure feed, recovery-rate trend, and per-transaction recovery status.

## Tech stack

| Layer | Stack |
|---|---|
| Backend | FastAPI, SQLAlchemy, httpx, deployed on Railway |
| Frontend | React, Vite, Tailwind CSS, Recharts, deployed on Vercel |
| AI | Gemini (primary), Groq (fallback), rule-based fallback (last resort) |
| Payments | Nomba Checkout API, HMAC-SHA256 signed webhooks |
| Testing | Pytest (backend), 19 automated tests covering signature verification, idempotency, and recovery flow |

## Structure

```
/backend   FastAPI service — webhook receiver, AI classification engine, Nomba API integration
/frontend  React dashboard — failure feed, recovery-rate chart, recovery triggers
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

See [`/backend/SECURITY.md`](./backend/SECURITY.md) for the full architecture and security write-up — HMAC-SHA256 webhook verification, idempotent event processing, sub-account scoping, and secret handling — as required by the hackathon submission rubric.

## Status

This is a **Stage 1 build-progress submission**, not a finished product. Webhook ingestion, AI classification, checkout generation, and the live dashboard are all working end-to-end against Nomba's sandbox environment. Still in progress: confirming real sandbox failure events flow through end-to-end now that our webhook URL and sub-account have been registered with Nomba, and a "payday retry" recovery-timing feature we've deliberately deferred to a later stage.
