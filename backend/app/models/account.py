from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.secret_box import EncryptedString
from app.models.base import Base, IdMixin, TimestampMixin, utc_now


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), nullable=False)
    username_key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    email_key: Mapped[str] = mapped_column(String(254), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(500))
    role: Mapped[str] = mapped_column(String(30), default="user", server_default="user", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="active", server_default="active", nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    # Encrypted at rest (app.core.secret_box); Python code sees the plain key.
    mfa_secret: Mapped[str | None] = mapped_column(EncryptedString(255))
    mfa_pending_secret: Mapped[str | None] = mapped_column(EncryptedString(255))
    require_password_reset: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(IdMixin, Base):
    __tablename__ = "user_sessions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    mfa_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)


class AuthToken(IdMixin, Base):
    __tablename__ = "auth_tokens"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    destination_email: Mapped[str | None] = mapped_column(String(254))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthIdentity(IdMixin, Base):
    __tablename__ = "auth_identities"
    __table_args__ = (UniqueConstraint("provider", "provider_subject", name="uq_auth_identity_provider_subject"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_email: Mapped[str | None] = mapped_column(String(254))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class UserPreference(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    theme: Mapped[str] = mapped_column(String(20), default="system", server_default="system", nullable=False)
    notifications_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class PlatformPolicy(Base):
    __tablename__ = "platform_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="global")
    feature_flags_json: Mapped[str] = mapped_column(Text, nullable=False)
    limits_json: Mapped[str] = mapped_column(Text, nullable=False)
    default_theme: Mapped[str] = mapped_column(String(20), default="system", nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class PlatformEmailSettings(Base):
    __tablename__ = "platform_email_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="global")
    sender_email: Mapped[str] = mapped_column(String(254), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class UserPolicyOverride(Base):
    __tablename__ = "user_policy_overrides"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    feature_overrides_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    limits_overrides_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"

    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AdminAuditEvent(Base):
    __tablename__ = "admin_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(120), index=True)
    result: Mapped[str] = mapped_column(String(20), default="success", nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    before_json: Mapped[str | None] = mapped_column(Text)
    after_json: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ReportEmailDelivery(IdMixin, TimestampMixin, Base):
    __tablename__ = "report_email_deliveries"
    __table_args__ = (UniqueConstraint("scope_id", "idempotency_key", "recipients_json", name="uq_report_email_request_recipient"),)

    scope_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    job_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_jobs.id"), index=True, nullable=False)
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id"), index=True, nullable=False)
    sender_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    recipients_json: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    delivery_method: Mapped[str] = mapped_column(String(20), nullable=False)
    include_summary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    transport: Mapped[str | None] = mapped_column(String(30))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReportAccessToken(IdMixin, Base):
    __tablename__ = "report_access_tokens"

    delivery_id: Mapped[str] = mapped_column(ForeignKey("report_email_deliveries.id"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(254), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkspaceImport(IdMixin, Base):
    __tablename__ = "workspace_imports"
    __table_args__ = (UniqueConstraint("source_scope", name="uq_workspace_import_source_scope"),)

    source_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    target_scope: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    imported_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    counts_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class WorkspaceQuota(Base):
    __tablename__ = "workspace_quotas"

    scope_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    storage_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    day_key: Mapped[str] = mapped_column(String(10), nullable=False)
    jobs_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    month_key: Mapped[str] = mapped_column(String(7), nullable=False)
    jobs_month: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    email_day_key: Mapped[str] = mapped_column(String(10), default="", nullable=False)
    emails_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


def _refuse_admin_audit_change(mapper, connection, target) -> None:
    raise PermissionError("Administrative audit events are append-only and cannot be modified.")


from sqlalchemy import DDL, event  # noqa: E402

event.listen(
    AdminAuditEvent.__table__, "after_create",
    DDL("CREATE TRIGGER IF NOT EXISTS admin_audit_no_update BEFORE UPDATE ON admin_audit_events "
        "BEGIN SELECT RAISE(ABORT, 'admin_audit_events is append-only'); END").execute_if(dialect="sqlite"),
)
event.listen(
    AdminAuditEvent.__table__, "after_create",
    DDL("CREATE TRIGGER IF NOT EXISTS admin_audit_no_delete BEFORE DELETE ON admin_audit_events "
        "BEGIN SELECT RAISE(ABORT, 'admin_audit_events is append-only'); END").execute_if(dialect="sqlite"),
)
event.listen(AdminAuditEvent, "before_update", _refuse_admin_audit_change)
event.listen(AdminAuditEvent, "before_delete", _refuse_admin_audit_change)
