"""Add registered accounts, policy controls, admin audit, and email records.

Revision ID: 20261009_0001
Revises: 20260929_0001
Create Date: 2026-10-09
"""

from alembic import op
import sqlalchemy as sa


revision = "20261009_0001"
down_revision = "20260929_0001"
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    for table in ("uploaded_files", "reports", "reconciliation_jobs", "reconciliation_history", "reconciliation_templates"):
        with op.batch_alter_table(table) as batch:
            batch.alter_column("session_id", existing_type=sa.String(length=36), type_=sa.String(length=64), nullable=False)
    with op.batch_alter_table("reconciliation_jobs") as batch:
        batch.add_column(sa.Column("display_name", sa.String(length=250), nullable=True))
        batch.add_column(sa.Column("quota_reserved", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.add_column(sa.Column("matched_records_count", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("exception_count", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("review_count", sa.Integer(), server_default="0", nullable=False))
    with op.batch_alter_table("uploaded_files") as batch:
        batch.add_column(sa.Column("row_count_total", sa.Integer(), server_default="0", nullable=False))
    with op.batch_alter_table("reports") as batch:
        batch.add_column(sa.Column("data_size_bytes", sa.Integer(), server_default="0", nullable=False))
    bind = op.get_bind()
    op.create_table(
        "review_outcomes",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("pair_key", sa.String(length=620), nullable=False),
        sa.Column("outcome", sa.String(length=10), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["reconciliation_jobs.id"]),
        sa.PrimaryKeyConstraint("job_id", "pair_key"),
    )
    match_table = sa.table(
        "match_decisions", sa.column("job_id", sa.String()), sa.column("pair_key", sa.String()),
        sa.column("decision", sa.String()), sa.column("created_at", sa.DateTime(timezone=True)),
    )
    decisions = bind.execute(
        sa.select(match_table.c.job_id, match_table.c.pair_key, match_table.c.decision, match_table.c.created_at)
        .where(match_table.c.job_id.is_not(None)).order_by(match_table.c.created_at)
    ).all()
    active_outcomes: dict[tuple[str, str], tuple[str, object]] = {}
    for job_id, pair_key, outcome, created_at in decisions:
        key = (job_id, pair_key)
        if outcome == "reset":
            active_outcomes.pop(key, None)
        else:
            active_outcomes[key] = (outcome, created_at)
    review_insert = sa.table(
        "review_outcomes", sa.column("job_id", sa.String()), sa.column("pair_key", sa.String()),
        sa.column("outcome", sa.String()), sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    for (job_id, pair_key), (outcome, updated_at) in active_outcomes.items():
        bind.execute(review_insert.insert().values(job_id=job_id, pair_key=pair_key, outcome=outcome, updated_at=updated_at))
    jobs_table = sa.table(
        "reconciliation_jobs", sa.column("id", sa.String()), sa.column("report_id", sa.String()),
        sa.column("matched_records_count", sa.Integer()), sa.column("exception_count", sa.Integer()), sa.column("review_count", sa.Integer()),
    )
    reports_table = sa.table("reports", sa.column("id", sa.String()), sa.column("summary_json", sa.Text()))
    for job_id, summary_json in bind.execute(sa.select(jobs_table.c.id, reports_table.c.summary_json).select_from(jobs_table.join(reports_table, jobs_table.c.report_id == reports_table.c.id))):
        try:
            import json

            summary = json.loads(summary_json or "{}")
            matched = int(summary.get("matched_records", summary.get("fully_matched_records", 0)) or 0)
            exceptions = int(summary.get("only_in_file_1", 0) or 0) + int(summary.get("only_in_file_2", 0) or 0) + int(summary.get("field_discrepancies", 0) or 0)
            review = int(summary.get("confidence_review", 0) or 0)
        except (TypeError, ValueError):
            matched, exceptions, review = 0, 0, 0
        bind.execute(jobs_table.update().where(jobs_table.c.id == job_id).values(matched_records_count=matched, exception_count=exceptions, review_count=review))

    resolved_by_job: dict[str, int] = {}
    for job_id, _pair_key in active_outcomes:
        resolved_by_job[job_id] = resolved_by_job.get(job_id, 0) + 1
    jobs_with_reviews = sa.table("reconciliation_jobs", sa.column("id", sa.String()), sa.column("review_count", sa.Integer()))
    for job_id, count in resolved_by_job.items():
        bind.execute(
            jobs_with_reviews.update().where(jobs_with_reviews.c.id == job_id).values(
                review_count=sa.case((jobs_with_reviews.c.review_count > count, jobs_with_reviews.c.review_count - count), else_=0)
            )
        )

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("username_key", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("email_key", sa.String(length=254), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=500), nullable=True),
        sa.Column("role", sa.String(length=30), server_default="user", nullable=False),
        sa.Column("status", sa.String(length=30), server_default="active", nullable=False),
        sa.Column("email_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("mfa_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("mfa_secret", sa.String(length=128), nullable=True),
        sa.Column("mfa_pending_secret", sa.String(length=128), nullable=True),
        sa.Column("require_password_reset", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username_key", name="uq_users_username_key"),
        sa.UniqueConstraint("email_key", name="uq_users_email_key"),
    )
    op.create_index("ix_users_username_key", "users", ["username_key"])
    op.create_index("ix_users_email_key", "users", ["email_key"])

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("mfa_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_token_hash", "user_sessions", ["token_hash"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])

    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=30), nullable=False),
        sa.Column("destination_email", sa.String(length=254), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_auth_tokens_token_hash"),
    )
    op.create_index("ix_auth_tokens_user_id", "auth_tokens", ["user_id"])
    op.create_index("ix_auth_tokens_token_hash", "auth_tokens", ["token_hash"])
    op.create_index("ix_auth_tokens_purpose", "auth_tokens", ["purpose"])
    op.create_index("ix_auth_tokens_expires_at", "auth_tokens", ["expires_at"])

    op.create_table(
        "auth_identities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("provider_subject", sa.String(length=255), nullable=False),
        sa.Column("provider_email", sa.String(length=254), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_subject", name="uq_auth_identity_provider_subject"),
    )
    op.create_index("ix_auth_identities_user_id", "auth_identities", ["user_id"])

    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("theme", sa.String(length=20), server_default="system", nullable=False),
        sa.Column("notifications_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "platform_policies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("feature_flags_json", sa.Text(), nullable=False),
        sa.Column("limits_json", sa.Text(), nullable=False),
        sa.Column("default_theme", sa.String(length=20), server_default="system", nullable=False),
        sa.Column("updated_by", sa.String(length=36), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "user_policy_overrides",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("feature_overrides_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("limits_overrides_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("updated_by", sa.String(length=36), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "auth_rate_limits",
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("fingerprint"),
    )

    op.create_table(
        "admin_audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=False),
        sa.Column("target_id", sa.String(length=120), nullable=True),
        sa.Column("result", sa.String(length=20), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("before_json", sa.Text(), nullable=True),
        sa.Column("after_json", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_admin_audit_events_event_id"),
    )
    op.create_index("ix_admin_audit_events_action", "admin_audit_events", ["action"])
    op.create_index("ix_admin_audit_events_target_id", "admin_audit_events", ["target_id"])
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("CREATE TRIGGER IF NOT EXISTS admin_audit_no_update BEFORE UPDATE ON admin_audit_events BEGIN SELECT RAISE(ABORT, 'admin_audit_events is append-only'); END")
        op.execute("CREATE TRIGGER IF NOT EXISTS admin_audit_no_delete BEFORE DELETE ON admin_audit_events BEGIN SELECT RAISE(ABORT, 'admin_audit_events is append-only'); END")
    elif bind.dialect.name == "postgresql":
        op.execute("CREATE OR REPLACE FUNCTION admin_audit_events_append_only() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'admin_audit_events is append-only'; END; $$ LANGUAGE plpgsql")
        op.execute("CREATE TRIGGER admin_audit_no_change BEFORE UPDATE OR DELETE ON admin_audit_events FOR EACH ROW EXECUTE FUNCTION admin_audit_events_append_only()")
        op.execute("CREATE TRIGGER admin_audit_no_truncate BEFORE TRUNCATE ON admin_audit_events FOR EACH STATEMENT EXECUTE FUNCTION admin_audit_events_append_only()")

    op.create_table(
        "report_email_deliveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("sender_user_id", sa.String(length=36), nullable=True),
        sa.Column("recipients_json", sa.Text(), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("delivery_method", sa.String(length=20), nullable=False),
        sa.Column("include_summary", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("transport", sa.String(length=30), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(["job_id"], ["reconciliation_jobs.id"]),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"]),
        sa.ForeignKeyConstraint(["sender_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_id", "idempotency_key", "recipients_json", name="uq_report_email_request_recipient"),
    )
    op.create_index("ix_report_email_deliveries_scope_id", "report_email_deliveries", ["scope_id"])
    op.create_index("ix_report_email_deliveries_job_id", "report_email_deliveries", ["job_id"])
    op.create_index("ix_report_email_deliveries_report_id", "report_email_deliveries", ["report_id"])
    op.create_index("ix_report_email_deliveries_sender_user_id", "report_email_deliveries", ["sender_user_id"])
    op.create_index("ix_report_email_deliveries_status", "report_email_deliveries", ["status"])

    op.create_table(
        "report_access_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("delivery_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("recipient_email", sa.String(length=254), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["delivery_id"], ["report_email_deliveries.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_report_access_tokens_token_hash"),
    )
    op.create_index("ix_report_access_tokens_delivery_id", "report_access_tokens", ["delivery_id"])
    op.create_index("ix_report_access_tokens_token_hash", "report_access_tokens", ["token_hash"])
    op.create_index("ix_report_access_tokens_expires_at", "report_access_tokens", ["expires_at"])

    op.create_table(
        "workspace_imports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_scope", sa.String(length=64), nullable=False),
        sa.Column("target_scope", sa.String(length=64), nullable=False),
        sa.Column("imported_by", sa.String(length=36), nullable=False),
        sa.Column("counts_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["imported_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_scope", name="uq_workspace_import_source_scope"),
    )
    op.create_index("ix_workspace_imports_target_scope", "workspace_imports", ["target_scope"])

    op.create_table(
        "workspace_quotas",
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("storage_bytes", sa.Integer(), nullable=False),
        sa.Column("day_key", sa.String(length=10), nullable=False),
        sa.Column("jobs_today", sa.Integer(), nullable=False),
        sa.Column("month_key", sa.String(length=7), nullable=False),
        sa.Column("jobs_month", sa.Integer(), nullable=False),
        sa.Column("active_jobs", sa.Integer(), nullable=False),
        sa.Column("email_day_key", sa.String(length=10), nullable=False),
        sa.Column("emails_today", sa.Integer(), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("scope_id"),
    )


def downgrade() -> None:
    op.drop_table("workspace_quotas")
    op.drop_table("review_outcomes")
    op.drop_index("ix_workspace_imports_target_scope", table_name="workspace_imports")
    op.drop_table("workspace_imports")
    op.drop_index("ix_report_access_tokens_expires_at", table_name="report_access_tokens")
    op.drop_index("ix_report_access_tokens_token_hash", table_name="report_access_tokens")
    op.drop_index("ix_report_access_tokens_delivery_id", table_name="report_access_tokens")
    op.drop_table("report_access_tokens")
    for index in ("ix_report_email_deliveries_status", "ix_report_email_deliveries_sender_user_id", "ix_report_email_deliveries_report_id", "ix_report_email_deliveries_job_id", "ix_report_email_deliveries_scope_id"):
        op.drop_index(index, table_name="report_email_deliveries")
    op.drop_table("report_email_deliveries")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS admin_audit_no_truncate ON admin_audit_events")
        op.execute("DROP TRIGGER IF EXISTS admin_audit_no_change ON admin_audit_events")
        op.execute("DROP FUNCTION IF EXISTS admin_audit_events_append_only()")
    elif bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS admin_audit_no_update")
        op.execute("DROP TRIGGER IF EXISTS admin_audit_no_delete")
    op.drop_index("ix_admin_audit_events_target_id", table_name="admin_audit_events")
    op.drop_index("ix_admin_audit_events_action", table_name="admin_audit_events")
    op.drop_table("admin_audit_events")
    op.drop_table("auth_rate_limits")
    op.drop_table("user_policy_overrides")
    op.drop_table("platform_policies")
    op.drop_table("user_preferences")
    op.drop_index("ix_auth_identities_user_id", table_name="auth_identities")
    op.drop_table("auth_identities")
    for index in ("ix_auth_tokens_expires_at", "ix_auth_tokens_purpose", "ix_auth_tokens_token_hash", "ix_auth_tokens_user_id"):
        op.drop_index(index, table_name="auth_tokens")
    op.drop_table("auth_tokens")
    for index in ("ix_user_sessions_expires_at", "ix_user_sessions_token_hash", "ix_user_sessions_user_id"):
        op.drop_index(index, table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index("ix_users_email_key", table_name="users")
    op.drop_index("ix_users_username_key", table_name="users")
    op.drop_table("users")
    with op.batch_alter_table("reconciliation_jobs") as batch:
        batch.drop_column("review_count")
        batch.drop_column("exception_count")
        batch.drop_column("matched_records_count")
        batch.drop_column("quota_reserved")
        batch.drop_column("display_name")
    with op.batch_alter_table("uploaded_files") as batch:
        batch.drop_column("row_count_total")
    with op.batch_alter_table("reports") as batch:
        batch.drop_column("data_size_bytes")
    for table in reversed(("uploaded_files", "reports", "reconciliation_jobs", "reconciliation_history", "reconciliation_templates")):
        with op.batch_alter_table(table) as batch:
            batch.alter_column("session_id", existing_type=sa.String(length=64), type_=sa.String(length=36), nullable=False)
