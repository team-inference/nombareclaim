# NombaReclaim

An intelligent failed payment recovery engine that intercepts Nomba webhook
failure events, classifies abandonment reasons with AI, and automatically
generates personalized recovery checkout flows to recapture lost merchant
revenue.

Built for the DevCareer x Nomba Hackathon 2026 by Team Inference.

- Aborowa Daniel Toluwanimi — Lead, Frontend, Product
- Elebiemayo Iseoluwa Emmanuel — Backend, AI, Security

## Structure

- `/backend` — FastAPI service: webhook receiver, classification engine, Nomba API integration
- `/frontend` — React dashboard

## Security note

See `/backend/SECURITY.md` for the architecture and security write-up
(HMAC verification, idempotency, secret handling) required by the
hackathon submission rubric.
