"""
Central settings object. Everything here is loaded from environment
variables. Nothing in this file should ever contain a real secret —
real values live in `.env` locally (gitignored) and in Railway's
environment variable dashboard in production.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # --- Nomba account scoping ---
    # Authenticate with the parent accountId, scope individual calls to
    # the sub-account id. Both are required per the credentials brief.
    NOMBA_ACCOUNT_ID: str = os.getenv("NOMBA_ACCOUNT_ID", "")
    NOMBA_SUBACCOUNT_ID: str = os.getenv("NOMBA_SUBACCOUNT_ID", "")

    # --- Nomba API auth ---
    # CONFIRMED against Nomba's official training material: sandbox and
    # production are SEPARATE HOSTS, not the same host with different
    # credentials (an earlier version of this file assumed the latter —
    # that was wrong).
    #   Sandbox (all hackathon work):  https://sandbox.api.nomba.com/v1
    #   Production (post-KYC only):    https://api.nomba.com/v1
    # Use the TEST client_id/private_key pair together with the sandbox
    # host for all development, the July 3 checkpoint, and the demo.
    NOMBA_CLIENT_ID: str = os.getenv("NOMBA_CLIENT_ID", "")
    NOMBA_PRIVATE_KEY: str = os.getenv("NOMBA_PRIVATE_KEY", "")
    NOMBA_API_BASE_URL: str = os.getenv("NOMBA_API_BASE_URL", "https://sandbox.api.nomba.com/v1")

    # --- Webhook verification ---
    # CONFIRMED against Nomba's official training documentation
    # (training.nomba.com > Webhooks): single header "nomba-signature",
    # HMAC-SHA256 over the raw request body. No timestamp header exists
    # in their scheme — replay protection is handled via idempotency on
    # event.requestId instead (see routes/webhooks.py), which is what
    # Nomba's own docs recommend.
    NOMBA_WEBHOOK_SIGNATURE_KEY: str = os.getenv("NOMBA_WEBHOOK_SIGNATURE_KEY", "")
    NOMBA_SIGNATURE_HEADER: str = os.getenv("NOMBA_SIGNATURE_HEADER", "nomba-signature")

    # --- AI ---
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    # Second-tier AI fallback: tried only if Gemini is unconfigured or
    # its call fails. See services/classification.py for the full chain.
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # --- App ---
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./nombareclaim.db")
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    ALLOWED_ORIGINS: list[str] = [
        o.strip()
        for o in os.getenv(
            "ALLOWED_ORIGINS", "http://localhost:5173"
        ).split(",")
        if o.strip()
    ]

    # Manual-trigger recovery score threshold. The score is advisory,
    # not a hard gate, for the manual dashboard trigger path — see
    # services/recovery.py.
    RECOVERY_SCORE_THRESHOLD: int = int(os.getenv("RECOVERY_SCORE_THRESHOLD", "60"))

    # Rate limiting on the trigger-recovery endpoint.
    RATE_LIMIT_MAX_REQUESTS: int = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "5"))
    RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))


settings = Settings()
