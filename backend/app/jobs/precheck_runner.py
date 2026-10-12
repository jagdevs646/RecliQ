"""Run one data-quality check outside the API process."""
from __future__ import annotations

import json
import logging
import os

from sqlalchemy import update

from app.database.session import SessionLocal, engine
from app.models.precheck import ReconciliationPrecheck
from app.services.audit_service import AuditActor, record_event
from app.services.precheck_service import precheck_summary, run_precheck
from app.schemas.reconciliation import ReconciliationPlan
from app.utils.json_encoder import safe_json_dumps

logger = logging.getLogger("recliq.precheck")
_ENGINE_PID = os.getpid()


def run_precheck_task(task_id: str) -> None:
    if os.getpid() != _ENGINE_PID:
        engine.dispose(close=False)
    db = SessionLocal()
    try:
        claimed = db.execute(
            update(ReconciliationPrecheck)
            .where(ReconciliationPrecheck.id == task_id, ReconciliationPrecheck.status == "queued")
            .values(status="processing")
        )
        db.commit()
        if claimed.rowcount != 1:
            return

        task = db.get(ReconciliationPrecheck, task_id)
        if task is None:
            return
        try:
            plan = ReconciliationPlan.model_validate_json(task.plan_json)
            result = run_precheck(db, task.session_id, plan)
            task.result_json = safe_json_dumps(result)
            task.status = "completed"
            task.error_message = None
            db.commit()
            record_event(
                scope_id=task.session_id,
                actor=AuditActor.system("precheck-worker"),
                action="precheck.completed",
                entity_type="precheck",
                entity_id=task.id,
                summary=f"Data-quality pre-check: {result['status']}",
                metadata=precheck_summary(result),
            )
        except Exception:
            db.rollback()
            logger.exception("Data-quality precheck failed", extra={"precheck_id": task_id})
            failed = db.get(ReconciliationPrecheck, task_id)
            if failed is not None:
                failed.status = "failed"
                failed.error_message = "The data check could not be completed. Try again; if it continues, contact support."
                db.commit()
                record_event(
                    scope_id=failed.session_id,
                    actor=AuditActor.system("precheck-worker"),
                    action="precheck.failed",
                    entity_type="precheck",
                    entity_id=failed.id,
                    summary="Data-quality pre-check failed",
                )
    finally:
        db.close()
