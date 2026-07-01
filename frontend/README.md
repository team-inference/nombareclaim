# NombaReclaim — Frontend

Merchant-facing dashboard for NombaReclaim, built by Team Inference for
the DevCareer x Nomba Hackathon 2026.

React (Vite) + Tailwind CSS v4 + Recharts.

## Local development

```bash
npm install
npm run dev
```

Runs against mock data by default (`VITE_USE_MOCKS=true` in `.env`),
so the whole dashboard is fully browsable and demoable without a
running backend.

## Pointing at the real backend

Edit `.env`:

```
VITE_API_BASE_URL=https://your-backend.up.railway.app
VITE_USE_MOCKS=false
```

Every screen reads through `src/api/failures.js`, which is the single
place that decides mock vs. real — no component code needs to change.

## API contract

This frontend is built against the shared contract in the repo root's
`00_master_prompt_team_brief.md` — `GET /api/summary`,
`GET /api/failures`, `GET /api/failures/{id}`,
`POST /api/failures/{id}/trigger-recovery`. If a real response ever
doesn't match that shape, that's a backend-side bug to flag, not
something to silently patch around here.

## Structure

```
src/
  api/          fetch wrapper + typed API calls (mock/real switch lives here)
  components/   presentational pieces (cards, list, detail panel, badges, chart)
  pages/        Dashboard.jsx composes everything
  mocks/        fixture data matching the API contract exactly
  lib/format.js currency, date, and label formatting helpers
```

## Deployment (Vercel)

- Root directory: `frontend`
- Framework preset: Vite
- Env vars (`VITE_API_BASE_URL`, `VITE_USE_MOCKS`): set in Vercel's
  project settings, not committed.
