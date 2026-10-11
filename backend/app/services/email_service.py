from __future__ import annotations

import logging
import json
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import get_settings


logger = logging.getLogger("recliq.email")
DEFAULT_SENDER_EMAIL = "jagdev.s65@gmail.com"
EMAIL_SETTINGS_ID = "global"


def configured_sender_email(db=None) -> str:
    """Return the admin override, then environment value, then the requested default."""
    settings = get_settings()
    owns_session = db is None
    if owns_session:
        try:
            from app.database.session import SessionLocal

            db = SessionLocal()
        except Exception:
            db = None
    try:
        if db is not None:
            from app.models.account import PlatformEmailSettings

            configured = db.get(PlatformEmailSettings, EMAIL_SETTINGS_ID)
            if configured and configured.sender_email.strip():
                return configured.sender_email.strip()
    except Exception as exc:
        logger.warning("Email sender override could not be read", extra={"error_type": type(exc).__name__})
    finally:
        if owns_session and db is not None:
            db.close()
    return (settings.smtp_from_email or DEFAULT_SENDER_EMAIL).strip()


def email_transport_status(db=None) -> dict[str, str | bool]:
    settings = get_settings()
    backend = settings.email_backend.lower()
    sender_email = configured_sender_email(db)
    local_ready = backend == "log" and settings.environment.lower() in {"development", "local", "test"}
    auth_incomplete = bool(settings.smtp_username and not settings.smtp_password)
    smtp_ready = backend == "smtp" and bool(settings.smtp_host and sender_email) and not auth_incomplete
    if backend == "smtp":
        transport = "smtp"
    elif local_ready:
        transport = "local_log"
    else:
        transport = "disabled"
    return {
        "sender_email": sender_email,
        "transport": transport,
        "ready": smtp_ready or local_ready,
        "smtp_ready": smtp_ready,
        "smtp_host_configured": bool(settings.smtp_host),
        "smtp_auth_configured": bool(settings.smtp_username and settings.smtp_password),
        "local_only": local_ready,
    }


def send_email(*, recipient: str, subject: str, text_body: str, attachments: list[tuple[str, bytes, str]] | None = None, log_body: bool = True) -> str:
    """Send through a configured SMTP transport or a dev-only log transport."""
    settings = get_settings()
    backend = settings.email_backend.lower()
    sender_email = configured_sender_email()
    if backend == "log" and settings.environment.lower() in {"development", "local", "test"}:
        logger.warning("LOCAL EMAIL sender=%s recipient=%s subject=%s body=%s", sender_email, recipient, subject, text_body if log_body else "[omitted]")
        return "local-log"
    if backend != "smtp" or not settings.smtp_host or not sender_email or (settings.smtp_username and not settings.smtp_password):
        raise RuntimeError("Email delivery is not configured. Set EMAIL_BACKEND=smtp and the SMTP settings.")

    message = EmailMessage()
    message["From"] = f"{settings.smtp_from_name} <{sender_email}>"
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text_body)
    for filename, content, media_type in attachments or []:
        maintype, _, subtype = media_type.partition("/")
        message.add_attachment(content, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=filename)

    context = ssl.create_default_context()
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        server.ehlo()
        if settings.smtp_starttls:
            server.starttls(context=context)
            server.ehlo()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password or "")
        refused = server.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    return message.get("Message-ID", "")


def notify_workspace(*, scope_id: str, event: str, job_id: str, run_name: str | None = None, delivery_status: str | None = None) -> None:
    """Send opted-in account status notifications without exposing report data."""
    if not scope_id.startswith("user-"):
        return
    from app.database.session import SessionLocal
    from app.models.account import User, UserPreference

    user_id = scope_id.removeprefix("user-")
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        preference = db.get(UserPreference, user_id)
        if not user or not user.email_verified or not preference:
            return
        try:
            choices = json.loads(preference.notifications_json or "{}")
        except json.JSONDecodeError:
            choices = {}
        if not choices.get(event):
            return
        label = run_name or f"Reconciliation {job_id[:8]}"
        link = f"{get_settings().public_app_url.rstrip('/')}/#/results/{job_id}"
        if event == "report_email_status":
            result = delivery_status if delivery_status in {"sent", "failed"} else "updated"
            subject = f"RecliQ report email {result}"
            body = f"Report delivery for {label} is {result}. Open your private RecliQ workspace for details:\n{link}"
        else:
            result = "completed" if event == "reconciliation_completed" else "failed"
            subject = f"RecliQ reconciliation {result}"
            body = f"{label} has {result}. Open your private RecliQ workspace for details:\n{link}"
        send_email(
            recipient=user.email,
            subject=subject,
            text_body=body,
            log_body=False,
        )
    except Exception as exc:
        logger.warning("Workspace notification could not be sent", extra={"job_id": job_id, "error_type": type(exc).__name__})
    finally:
        db.close()
