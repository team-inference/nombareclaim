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
    FRONTEND_BASE_URL: str = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173")

    # Manual-trigger recovery score threshold. The score is advisory,
    # not a hard gate, for the manual dashboard trigger path — see
    # services/recovery.py.
    RECOVERY_SCORE_THRESHOLD: int = int(os.getenv("RECOVERY_SCORE_THRESHOLD", "60"))

    # Rate limiting on the trigger-recovery endpoint.
    RATE_LIMIT_MAX_REQUESTS: int = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "5"))
    RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

    # --- Webhook event type names ---
    # Genuinely unresolved by any doc source seen so far (see the long
    # comment in routes/webhooks.py) — kept as an env var rather than a
    # hardcoded constant specifically so it can be corrected the moment
    # it's confirmed (e.g. from Nomba's webhook-registration event
    # picker, or from a real sandbox test), with zero code changes and
    # no redeploy of anything but an env var.
    NOMBA_FAILURE_EVENT_TYPES: set = {
        s.strip().upper()
        for s in os.getenv("NOMBA_FAILURE_EVENT_TYPES", "PAYMENT_FAILED").split(",")
        if s.strip()
    }
    NOMBA_SUCCESS_EVENT_TYPES: set = {
        s.strip().upper()
        for s in os.getenv("NOMBA_SUCCESS_EVENT_TYPES", "PAYMENT_SUCCESS").split(",")
        if s.strip()
    }

    # --- Automated recovery (email) ---
    # Fully-automatic recovery is a hard-gated, opt-in path, separate
    # from the manual dashboard "trigger recovery" button (which has no
    # score gate — a merchant/judge can always override). Automation
    # only fires when there IS a channel to reach the customer on
    # (customer_email captured from the webhook) AND the score clears
    # this threshold. Off by default so a fresh deploy never silently
    # emails real customers before someone deliberately turns it on.
    RECOVERY_AUTOMATION_ENABLED: bool = os.getenv("RECOVERY_AUTOMATION_ENABLED", "false").lower() == "true"
    AUTO_RECOVERY_MIN_SCORE: int = int(os.getenv("AUTO_RECOVERY_MIN_SCORE", "40"))

    # --- Follow-up retry scheduling ("payday retry") ---
    # INSUFFICIENT_FUNDS failures are followed up around Nigeria's
    # common salary-payment window rather than a short fixed delay,
    # since a wallet that was empty is far more likely to succeed once
    # the customer has been paid than it is three hours later. Every
    # other classification uses a short fixed backoff instead. Both
    # stop after MAX_AUTO_RETRIES. See services/scheduling.py.
    PAYDAY_RETRY_DAYS: list[int] = [
        int(d) for d in os.getenv("PAYDAY_RETRY_DAYS", "25,26,27,28,29,30,31,1").split(",") if d.strip()
    ]
    RETRY_BACKOFF_HOURS: list[int] = [
        int(h) for h in os.getenv("RETRY_BACKOFF_HOURS", "3,24,72").split(",") if h.strip()
    ]
    MAX_AUTO_RETRIES: int = int(os.getenv("MAX_AUTO_RETRIES", "3"))
    RETRY_SWEEP_INTERVAL_SECONDS: int = int(os.getenv("RETRY_SWEEP_INTERVAL_SECONDS", "300"))

    # --- Outbound email (SMTP) ---
    # Deliberately plain smtplib rather than a provider SDK — works
    # with a Gmail app password, Zoho, or any real SMTP relay without
    # adding a new dependency. If left unconfigured, send_recovery_email
    # logs and returns False rather than raising, so the rest of the
    # pipeline (classification, checkout link generation, dashboard)
    # keeps working with automation simply never firing.
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", "")
    SMTP_FROM_NAME: str = os.getenv("SMTP_FROM_NAME", "NombaReclaim")


settings = Settings()
