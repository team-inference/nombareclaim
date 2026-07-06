"""
Outbound recovery notifications.

Plain stdlib smtplib rather than a provider SDK — works with a Gmail
app password, Zoho, SendGrid's SMTP relay, or anything else that
speaks SMTP, without adding a new dependency to requirements.txt.

Same rule as services/classification.py's AI provider chain: this
must never raise and never block the caller on a slow/broken mail
server for long. If SMTP isn't configured, or the send fails for any
reason, this logs and returns False — the pipeline keeps working
end-to-end, automation just doesn't fire for that attempt.
"""
import logging
import smtplib
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger("nombareclaim.notifications")


def send_recovery_email(to_email: str, subject: str, body: str) -> bool:
    if not to_email:
        return False
    if not (settings.SMTP_HOST and settings.SMTP_USERNAME and settings.SMTP_PASSWORD and settings.SMTP_FROM_EMAIL):
        logger.info("SMTP not configured — skipping recovery email to %s", to_email)
        return False

    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
        msg["To"] = to_email

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM_EMAIL, [to_email], msg.as_string())
        return True
    except Exception:
        logger.exception("failed to send recovery email to %s", to_email)
        return False
