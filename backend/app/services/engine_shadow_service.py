"""Controlled, private comparison of the row and column reconciliation engines.

This is rollout evidence, not a second user-facing reconciliation.  A job
continues to publish the row-engine result while this service records whether
the candidate column engine reached the same universal result data.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.audit import AuditEvent
from app.models.engine_shadow import EngineShadowComparison
from app.models.job import ReconciliationJob
from app.reconciliation_engine.result_comparison import iter_result_differences
from app.services.audit_service import AuditActor, record_event
from app.storage.base import StorageBackend
from app.utils.json_encoder import safe_json_dumps


logger = logging.getLogger("recliq.engine_shadow")
SHADOW_AUDIT_ACTION = "engine.shadow.completed"
_DETAILS_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ShadowDecision:
    enabled: bool
    reason: str


def shadow_decision(db: Session, settings: Settings, job: ReconciliationJob) -> ShadowDecision:
    """Say whether this job belongs to the currently configured rollout."""
    if not settings.engine_shadow_enabled:
        return ShadowDecision(False, "disabled")
    if job.job_type != "generic":
        return ShadowDecision(False, "unsupported_job_type")
    if settings.engine != "rows":
        return ShadowDecision(False, "primary_engine_is_not_rows")

    now = datetime.now(timezone.utc)
    started_at = settings.engine_shadow_started_at
    if started_at is not None:
        started_utc = started_at.replace(tzinfo=timezone.utc) if started_at.tzinfo is None else started_at.astimezone(timezone.utc)
        if now < started_utc:
            return ShadowDecision(False, "rollout_not_started")

    until = settings.engine_shadow_until
    if until is not None:
        until_utc = until.replace(tzinfo=timezone.utc) if until.tzinfo is None else until.astimezone(timezone.utc)
        if now >= until_utc:
            return ShadowDecision(False, "rollout_expired")

    if settings.engine_shadow_max_jobs:
        completed_query = db.query(AuditEvent.id).filter(AuditEvent.action == SHADOW_AUDIT_ACTION)
        if started_at is not None:
            # SQLite stores its DateTime values without offsets. PostgreSQL
            # keeps the aware UTC instant, so use the matching bound per DB.
            audit_cutoff = started_utc.replace(tzinfo=None) if db.get_bind().dialect.name == "sqlite" else started_utc
            completed_query = completed_query.filter(AuditEvent.occurred_at >= audit_cutoff)
        completed = completed_query.count()
        if completed >= settings.engine_shadow_max_jobs:
            return ShadowDecision(False, "job_limit_reached")
    return ShadowDecision(True, "enabled")


def shadow_details_storage_path(session_id: str, job_id: str) -> str:
    """Keep diagnostic evidence isolated from reports and from other sessions."""
    return f"engine-shadow/{session_id}/{job_id}-differences.jsonl"


@dataclass
class EngineShadowTracker:
    """Accumulate a job's semantic engine comparison without retaining diffs."""

    job: ReconciliationJob
    details_path: Path
    primary_seconds: float = 0.0
    shadow_seconds: float = 0.0
    comparison_seconds: float = 0.0
    rule_count: int = 0
    compared_rule_count: int = 0
    skipped_rule_count: int = 0
    difference_count: int = 0
    _failure_reasons: list[str] = field(default_factory=list)
    _details_handle: Any = field(init=False, repr=False)

    @classmethod
    def create(cls, job: ReconciliationJob, work_dir: Path) -> "EngineShadowTracker":
        work_dir.mkdir(parents=True, exist_ok=True)
        return cls(job=job, details_path=work_dir / f"{job.id}-engine-shadow-differences.jsonl")

    def __post_init__(self) -> None:
        self._details_handle = self.details_path.open("w", encoding="utf-8")

    def _record_failure(self, rule_id: str, error_type: str) -> None:
        if len(self._failure_reasons) < 20:
            self._failure_reasons.append(f"{rule_id}: {error_type}")

    def skip_rule(self) -> None:
        self.rule_count += 1
        self.skipped_rule_count += 1

    def mark_primary_failure(self, rule_id: str) -> None:
        self.rule_count += 1
        self.skipped_rule_count += 1
        self._record_failure(rule_id, "primary_engine_failed")

    def compare_rule(
        self,
        *,
        file_pair_id: str,
        sheet_rule_id: str,
        report_label: str,
        primary_result: dict[str, Any],
        primary_seconds: float,
        run_shadow: Callable[[], dict[str, Any]],
    ) -> None:
        """Run the candidate engine and write every mismatch as one JSONL row."""
        self.rule_count += 1
        self.primary_seconds += max(0.0, primary_seconds)
        shadow_started = time.perf_counter()
        try:
            shadow_result = run_shadow()
        except InterruptedError:
            raise
        except Exception as exc:
            self.shadow_seconds += time.perf_counter() - shadow_started
            self._record_failure(sheet_rule_id, type(exc).__name__)
            logger.warning(
                "Column engine shadow run failed",
                extra={"job_id": self.job.id, "sheet_rule_id": sheet_rule_id, "error_type": type(exc).__name__},
            )
            return
        self.shadow_seconds += time.perf_counter() - shadow_started

        comparison_started = time.perf_counter()
        try:
            for difference in iter_result_differences(
                primary_result.get("universal_data"),
                shadow_result.get("universal_data"),
            ):
                self._details_handle.write(safe_json_dumps({
                    "schema_version": _DETAILS_SCHEMA_VERSION,
                    "file_pair_id": file_pair_id,
                    "sheet_rule_id": sheet_rule_id,
                    "report_label": report_label,
                    "difference": difference.as_dict(),
                }, ensure_ascii=False, separators=(",", ":")))
                self._details_handle.write("\n")
                self.difference_count += 1
        except InterruptedError:
            raise
        except Exception as exc:
            self._record_failure(sheet_rule_id, type(exc).__name__)
            logger.warning(
                "Engine shadow comparison could not be recorded",
                extra={"job_id": self.job.id, "sheet_rule_id": sheet_rule_id, "error_type": type(exc).__name__},
            )
            return
        finally:
            self.comparison_seconds += time.perf_counter() - comparison_started
        self.compared_rule_count += 1

    def finalize(self, db: Session, storage: StorageBackend) -> EngineShadowComparison:
        """Persist the compact rollout result and, only when needed, its JSONL."""
        if not self._details_handle.closed:
            self._details_handle.flush()
            self._details_handle.close()

        status = "failed" if self._failure_reasons else "skipped"
        if not self._failure_reasons and self.compared_rule_count:
            status = "different" if self.difference_count else "matched"

        storage_path: str | None = None
        details_size_bytes = 0
        if self.details_path.exists() and self.details_path.stat().st_size:
            candidate_path = shadow_details_storage_path(self.job.session_id, self.job.id)
            try:
                storage.save_file(self.details_path, candidate_path)
                storage_path = candidate_path
                details_size_bytes = self.details_path.stat().st_size
            except Exception as exc:
                self._record_failure("artifact", type(exc).__name__)
                status = "failed"
                logger.warning(
                    "Engine shadow details could not be persisted",
                    extra={"job_id": self.job.id, "error_type": type(exc).__name__},
                )

        comparison = EngineShadowComparison(
            job_id=self.job.id,
            session_id=self.job.session_id,
            primary_engine="rows",
            shadow_engine="columns",
            status=status,
            rule_count=self.rule_count,
            compared_rule_count=self.compared_rule_count,
            skipped_rule_count=self.skipped_rule_count,
            difference_count=self.difference_count,
            primary_seconds=round(self.primary_seconds, 6),
            shadow_seconds=round(self.shadow_seconds, 6),
            comparison_seconds=round(self.comparison_seconds, 6),
            details_storage_path=storage_path,
            details_size_bytes=details_size_bytes,
            failure_summary="; ".join(self._failure_reasons)[:2000] or None,
            completed_at=datetime.now(timezone.utc),
        )
        db.add(comparison)
        return comparison

    def close(self) -> None:
        if not self._details_handle.closed:
            self._details_handle.close()


def record_shadow_audit(comparison: EngineShadowComparison) -> None:
    """Write bounded, non-sensitive shadow facts into the append-only audit log."""
    summary = {
        "matched": "Column engine shadow result matched the row-engine result.",
        "different": "Column engine shadow result differed from the row-engine result.",
        "failed": "Column engine shadow comparison could not be completed.",
        "skipped": "Column engine shadow comparison had no eligible sheet rules.",
    }[comparison.status]
    record_event(
        scope_id=comparison.session_id,
        actor=AuditActor.system(),
        action=SHADOW_AUDIT_ACTION,
        entity_type="engine_shadow_comparison",
        entity_id=comparison.id,
        summary=summary,
        after={
            "job_id": comparison.job_id,
            "status": comparison.status,
            "difference_count": comparison.difference_count,
        },
        metadata={
            "primary_engine": comparison.primary_engine,
            "shadow_engine": comparison.shadow_engine,
            "rule_count": comparison.rule_count,
            "compared_rule_count": comparison.compared_rule_count,
            "skipped_rule_count": comparison.skipped_rule_count,
            "primary_seconds": comparison.primary_seconds,
            "shadow_seconds": comparison.shadow_seconds,
            "comparison_seconds": comparison.comparison_seconds,
            "details_available": bool(comparison.details_storage_path),
            "failure_summary": comparison.failure_summary,
        },
    )
