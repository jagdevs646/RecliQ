from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from sqlalchemy import update

from app.core.config import get_settings
from app.database.session import SessionLocal
from app.models.account import ReportAccessToken, ReportEmailDelivery
from app.models.job import ReconciliationJob
from app.models.report import Report
from app.services.audit_service import AuditActor, record_event
from app.services.email_service import send_email
from app.storage import get_storage


logger = logging.getLogger("recliq.email")


def _summary_lines(summary_json: str) -> str:
    try:
        summary = json.loads(summary_json or "{}")
    except json.JSONDecodeError:
        summary = {}
    labels = (
        ("matched_records", "Matched records"),
        ("only_in_file_1", "Only in first file"),
        ("only_in_file_2", "Only in second file"),
        ("field_discrepancies", "Field discrepancies"),
        ("confidence_review", "Matches awaiting review"),
    )
    lines = [f"{label}: {int(summary.get(key, 0) or 0):,}" for key, label in labels]
    return "Reconciliation summary\n" + "\n".join(lines)


def process_email_delivery(delivery_id: str) -> None:
    """Deliver one recipient; a conditional claim makes duplicate queue messages harmless."""
    db = SessionLocal()
    try:
        claimed = db.execute(
            update(ReportEmailDelivery)
            .where(ReportEmailDelivery.id == delivery_id, ReportEmailDelivery.status == "queued")
            .values(status="processing", attempt_count=ReportEmailDelivery.attempt_count + 1)
        ).rowcount
        db.commit()
        if not claimed:
            return

        delivery = db.get(ReportEmailDelivery, delivery_id)
        report = db.query(Report).filter(
            Report.id == delivery.report_id,
            Report.session_id == delivery.scope_id,
        ).first() if delivery else None
        job = db.query(ReconciliationJob).filter(
            ReconciliationJob.id == delivery.job_id,
            ReconciliationJob.session_id == delivery.scope_id,
        ).first() if delivery else None
        if not delivery or not report or not job:
            raise RuntimeError("The report is no longer available in this workspace.")

        recipients = json.loads(delivery.recipients_json)
        if len(recipients) != 1:
            raise RuntimeError("A delivery record must contain exactly one recipient.")
        recipient = recipients[0]
        attachments = None
        body_parts = [delivery.message.strip()] if delivery.message.strip() else []
        if delivery.include_summary:
            body_parts.append(_summary_lines(report.summary_json))

        if delivery.delivery_method == "attachment":
            path = get_storage().resolve_path(report.storage_path)
            content = path.read_bytes()
            if len(content) > get_settings().max_upload_bytes:
                raise RuntimeError("The report is larger than the configured email attachment limit. Use a secure download link.")
            media_type = "application/zip" if path.suffix.lower() == ".zip" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            attachments = [(report.filename, content, media_type)]
            body_parts.insert(0, "Your RecliQ reconciliation report is attached.")
        else:
            db.query(ReportAccessToken).filter(
                ReportAccessToken.delivery_id == delivery.id,
                ReportAccessToken.revoked_at.is_(None),
            ).update({ReportAccessToken.revoked_at: datetime.now(timezone.utc)}, synchronize_session=False)
            raw_token = secrets.token_urlsafe(48)
            now = datetime.now(timezone.utc)
            db.add(ReportAccessToken(
                delivery_id=delivery.id,
                token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
                recipient_email=recipient,
                created_at=now,
                expires_at=now + timedelta(hours=max(1, get_settings().report_link_hours)),
            ))
            db.commit()
            settings = get_settings()
            api_root = (settings.public_api_url or f"{settings.public_app_url.rstrip('/')}{settings.api_prefix}").rstrip("/")
            link = f"{api_root}/reports/shared/{quote(raw_token)}"
            body_parts.insert(0, f"Download your RecliQ report using this recipient-specific link:\n{link}\n\nThe link expires in {max(1, settings.report_link_hours)} hour(s).")

        transport_id = send_email(
            recipient=recipient,
            subject=delivery.subject,
            text_body="\n\n".join(part for part in body_parts if part),
            attachments=attachments,
            log_body=False,
        )
        delivery.status = "sent"
        delivery.provider_message_id = transport_id
        delivery.transport = "local_log" if transport_id == "local-log" else "smtp"
        delivery.sent_at = datetime.now(timezone.utc)
        delivery.error_message = None
        db.commit()
        record_event(
            scope_id=delivery.scope_id,
            actor=AuditActor.system("email-worker"),
            action="report.email_sent",
            entity_type="job",
            entity_id=delivery.job_id,
            summary="Reconciliation report email accepted by the configured transport",
            after={"delivery_id": delivery.id, "method": delivery.delivery_method, "transport": delivery.transport},
        )
        from app.services.email_service import notify_workspace
        notify_workspace(scope_id=delivery.scope_id, event="report_email_status", job_id=delivery.job_id, run_name=delivery.subject, delivery_status="sent")
    except Exception as exc:
        db.rollback()
        delivery = db.get(ReportEmailDelivery, delivery_id)
        if delivery and delivery.status == "processing":
            delivery.status = "failed"
            delivery.error_message = f"Delivery failed ({type(exc).__name__}). Check the destination and email configuration before retrying."
            delivery.transport = None
            db.query(ReportAccessToken).filter(
                ReportAccessToken.delivery_id == delivery.id,
                ReportAccessToken.revoked_at.is_(None),
            ).update({ReportAccessToken.revoked_at: datetime.now(timezone.utc)}, synchronize_session=False)
            db.commit()
            record_event(
                scope_id=delivery.scope_id,
                actor=AuditActor.system("email-worker"),
                action="report.email_failed",
                entity_type="job",
                entity_id=delivery.job_id,
                summary="Reconciliation report email delivery failed",
                after={"delivery_id": delivery.id, "error_type": type(exc).__name__},
            )
            from app.services.email_service import notify_workspace
            notify_workspace(scope_id=delivery.scope_id, event="report_email_status", job_id=delivery.job_id, run_name=delivery.subject, delivery_status="failed")
        logger.warning("Report email delivery failed", extra={"delivery_id": delivery_id, "error_type": type(exc).__name__})
    finally:
        db.close()
