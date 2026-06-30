"""
Central settings object. Everything here is loaded from environment
variables. Nothing in this file should ever contain a real secret —
real values live in `.env` locally (gitignored) and in Render's
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
    # Nomba does not use separate sandbox/live hostnames — the same
    # https://api.nomba.com/v1 base URL is used for both. Which mode
    # you are in is determined entirely by which client_id / private_key
    # pair you put in these two env vars. Use the TEST pair for all
    # development, the July 3 checkpoint, and the demo.
    NOMBA_CLIENT_ID: str = os.getenv("NOMBA_CLIENT_ID", "")
    NOMBA_PRIVATE_KEY: str = os.getenv("NOMBA_PRIVATE_KEY", "")
    NOMBA_API_BASE_URL: str = os.getenv("NOMBA_API_BASE_URL", "https://api.nomba.com/v1")

    # --- Webhook verification ---
    NOMBA_WEBHOOK_SIGNATURE_KEY: str = os.getenv("NOMBA_WEBHOOK_SIGNATURE_KEY", "")

    # The exact header names Nomba sends the signature/timestamp in were
    # not confirmable from public docs alone (the docs page renders the
    # sample code client-side). Confirm these against a real test webhook
    # delivery in your Nomba dashboard (Webhooks > Logs > a delivery will
    # show you the literal headers sent) and adjust the env vars below if
    # they differ — no code change needed, just update these two values.
    NOMBA_SIGNATURE_HEADER: str = os.getenv("NOMBA_SIGNATURE_HEADER", "signature")
    NOMBA_TIMESTAMP_HEADER: str = os.getenv("NOMBA_TIMESTAMP_HEADER", "timestamp")
    REPLAY_WINDOW_SECONDS: int = int(os.getenv("REPLAY_WINDOW_SECONDS", "300"))

    # --- AI ---
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

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
