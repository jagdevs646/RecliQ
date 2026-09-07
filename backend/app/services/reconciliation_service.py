from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import SessionLocal
from app.models.file import UploadedFile
from app.models.job import ReconciliationJob
from app.models.report import Report
from app.reconciliation_engine.background_jobs import run_in_background
from app.reconciliation_engine.engine import (
    read_excel_columns,
    run_generic_reconciliation,
    run_gst_reconciliation,
)
from app.schemas.reconciliation import GenericReconciliationRequest, GSTReconciliationRequest
from app.services.job_service import append_history
from app.storage import get_storage


def _file_record(db: Session, file_id: str, session_id: str) -> UploadedFile:
    record = db.query(UploadedFile).filter(UploadedFile.id == file_id, UploadedFile.session_id == session_id).first()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File not found: {file_id}")
    return record


def enqueue_generic_job(
    db: Session,
    payload: GenericReconciliationRequest,
    background_tasks: BackgroundTasks,
    session_id: str,
) -> ReconciliationJob:
    file_1_id = payload.file_1_id or (payload.source_files_1[0].file_id if payload.source_files_1 else None)
    file_2_id = payload.file_2_id or (payload.source_files_2[0].file_id if payload.source_files_2 else None)

    if not file_1_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source file 1 is required")
    if not file_2_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source file 2 is required")

    files_to_check = {file_1_id, file_2_id}
    for fs in payload.source_files_1:
        if fs.file_id:
            files_to_check.add(fs.file_id)
    for fs in payload.source_files_2:
        if fs.file_id:
            files_to_check.add(fs.file_id)

    for fid in files_to_check:
        _file_record(db, fid, session_id)

    job = ReconciliationJob(
        session_id=session_id,
        job_type="generic",
        status="queued",
        progress=0,
        orientation=payload.orientation,
        input_file_1_id=file_1_id,
        input_file_2_id=file_2_id,
        settings_json=payload.model_dump_json(),
    )
    db.add(job)
    db.flush()
    append_history(db, job, "queued", "Generic reconciliation job queued")
    db.commit()
    db.refresh(job)
    background_tasks.add_task(process_reconciliation_job_async, job.id)
    return job


def enqueue_gst_job(
    db: Session,
    payload: GSTReconciliationRequest,
    background_tasks: BackgroundTasks,
    session_id: str,
) -> ReconciliationJob:
    file_1_id = payload.file_1_id or (payload.source_files_1[0].file_id if payload.source_files_1 else None)
    file_2_id = payload.file_2_id or (payload.source_files_2[0].file_id if payload.source_files_2 else None)

    if not file_1_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source file 1 is required")
    if not file_2_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source file 2 is required")

    files_to_check = {file_1_id, file_2_id}
    for fs in payload.source_files_1:
        if fs.file_id:
            files_to_check.add(fs.file_id)
    for fs in payload.source_files_2:
        if fs.file_id:
            files_to_check.add(fs.file_id)

    for fid in files_to_check:
        _file_record(db, fid, session_id)

    job = ReconciliationJob(
        session_id=session_id,
        job_type="gst",
        status="queued",
        progress=0,
        orientation=payload.orientation,
        input_file_1_id=file_1_id,
        input_file_2_id=file_2_id,
        settings_json=payload.model_dump_json(),
    )
    db.add(job)
    db.flush()
    append_history(db, job, "queued", "GST reconciliation job queued")
    db.commit()
    db.refresh(job)
    background_tasks.add_task(process_reconciliation_job_async, job.id)
    return job


from app.reconciliation_engine.ingestion import extract_file_metadata, read_table_data
from app.reconciliation_engine.preprocessing import prepare_dataframe
from app.reconciliation_engine.cache import normalize_header


def get_file_metadata(db: Session, file_id: str, session_id: str) -> list[dict]:
    record = _file_record(db, file_id, session_id)
    path = get_storage().resolve_path(record.storage_path)
    return extract_file_metadata(path, record.original_filename)


def get_file_columns(db: Session, file_id: str, session_id: str, sheet_id: str | None = None, orientation: str = "vertical") -> list[str]:
    record = _file_record(db, file_id, session_id)
    path = get_storage().resolve_path(record.storage_path)
    
    if not sheet_id:
        # Default to first sheet
        metadata = extract_file_metadata(path, record.original_filename)
        sheet_id = metadata[0]["id"] if metadata else "default"
        
    df = read_table_data(path, record.original_filename, sheet_id)
    if df.empty:
        return []
        
    if str(orientation).lower().startswith("horizontal"):
        from app.reconciliation_engine.preprocessing import transform_horizontal_dataframe
        df = transform_horizontal_dataframe(df)
        
    return [normalize_header(col) for col in df.columns]


def process_reconciliation_job_async(job_id: str) -> None:
    # FastAPI already runs sync background tasks in a thread off the event loop.
    # Calling directly avoids double-threading (submit + future.result()) which
    # caused hangs with multi-worker Uvicorn deployments.
    process_reconciliation_job(job_id)


def process_reconciliation_job(job_id: str) -> None:
    db = SessionLocal()
    settings = get_settings()
    storage = get_storage()
    file_1_path: Path | None = None
    file_2_path: Path | None = None
    output_path: Path | None = None
    try:
        job = db.get(ReconciliationJob, job_id)
        if not job:
            return

        if job.status == "cancelled":
            return

        job.status = "processing"
        job.progress = 10
        job.started_at = datetime.now(timezone.utc)
        append_history(db, job, "processing", "Reconciliation started")
        db.commit()

        def is_cancelled() -> bool:
            try:
                check_db = SessionLocal()
                j = check_db.get(ReconciliationJob, job_id)
                cancelled = j is not None and j.status == "cancelled"
                check_db.close()
                return cancelled
            except Exception:
                return False

        def on_progress(percent: int, step_msg: str) -> None:
            nonlocal db, job_id
            try:
                sub_db = SessionLocal()
                j = sub_db.get(ReconciliationJob, job_id)
                if j and j.status != "cancelled":
                    j.progress = percent
                    append_history(sub_db, j, "processing", step_msg)
                    sub_db.commit()
                sub_db.close()
            except Exception:
                pass

        file_1 = (
            db.query(UploadedFile)
            .filter(UploadedFile.id == job.input_file_1_id, UploadedFile.session_id == job.session_id)
            .first()
        )
        file_2 = (
            db.query(UploadedFile)
            .filter(UploadedFile.id == job.input_file_2_id, UploadedFile.session_id == job.session_id)
            .first()
        )
        if not file_1 or not file_2:
            raise RuntimeError("One or both source files are missing")

        file_1_path = storage.resolve_path(file_1.storage_path)
        file_2_path = storage.resolve_path(file_2.storage_path)
        work_dir = Path(settings.local_storage_path) / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        output_name = "GST_Reconciliation.xlsx" if job.job_type == "gst" else "Reconciliation.xlsx"
        output_path = work_dir / f"{job.id}-{output_name}"
        payload = json.loads(job.settings_json or "{}")

        if is_cancelled():
            raise InterruptedError("Reconciliation cancelled by user")

        from app.api.routes.analysis import _load_and_consolidate
        from app.schemas.reconciliation import FileSource

        # Use pairs if present, otherwise fallback to legacy file_id
        pairs = payload.get("pairs", [])
        if not pairs:
            source_files_1 = payload.get("source_files_1") or [{"file_id": payload.get("file_1_id") or job.input_file_1_id}]
            source_files_2 = payload.get("source_files_2") or [{"file_id": payload.get("file_2_id") or job.input_file_2_id}]
            sources_1 = [FileSource(**fs) if isinstance(fs, dict) else fs for fs in source_files_1 if (fs.get("file_id") if isinstance(fs, dict) else getattr(fs, "file_id", None))]
            sources_2 = [FileSource(**fs) if isinstance(fs, dict) else fs for fs in source_files_2 if (fs.get("file_id") if isinstance(fs, dict) else getattr(fs, "file_id", None))]
            if not sources_1 and job.input_file_1_id:
                sources_1 = [FileSource(file_id=job.input_file_1_id)]
            if not sources_2 and job.input_file_2_id:
                sources_2 = [FileSource(file_id=job.input_file_2_id)]
                
            # Construct a dummy pair for backwards compatibility
            pairs = [{
                "source_file_1": sources_1[0].model_dump() if hasattr(sources_1[0], "model_dump") else (sources_1[0] if isinstance(sources_1[0], dict) else {"file_id": sources_1[0].file_id, "sheet_id": sources_1[0].sheet_id}),
                "source_file_2": sources_2[0].model_dump() if hasattr(sources_2[0], "model_dump") else (sources_2[0] if isinstance(sources_2[0], dict) else {"file_id": sources_2[0].file_id, "sheet_id": sources_2[0].sheet_id}),
                "key_file_1": payload.get("key_file_1"),
                "key_file_2": payload.get("key_file_2"),
                "rules": payload.get("rules", []),
                "include_columns_file_1": payload.get("include_columns_file_1", []),
                "include_columns_file_2": payload.get("include_columns_file_2", [])
            }]

        all_universal_data = []
        overall_summary = {
            "report_rows": 0, "only_in_file_1": 0, "only_in_file_2": 0, 
            "confidence_review": 0, "source_records": 0, "destination_records": 0,
            "matched_records": 0, "fully_matched_records": 0
        }

        # Iterating through sheet pairs
        for idx, pair in enumerate(pairs):
            s1 = FileSource(**pair["source_file_1"]) if isinstance(pair["source_file_1"], dict) else pair["source_file_1"]
            s2 = FileSource(**pair["source_file_2"]) if isinstance(pair["source_file_2"], dict) else pair["source_file_2"]
            
            df1 = _load_and_consolidate(db, job.session_id, [s1])
            df2 = _load_and_consolidate(db, job.session_id, [s2])
            
            sheet_name_1 = s1.sheet_id or "default"
            sheet_name_2 = s2.sheet_id or "default"
            pair_label = f"[{sheet_name_1} <-> {sheet_name_2}]"

            if job.job_type == "gst":
                res = run_gst_reconciliation(
                    file_1_df=df1,
                    file_2_df=df2,
                    output_path=output_path,
                    orientation=payload.get("orientation", job.orientation),
                    text_threshold=int(payload.get("text_threshold", 85)),
                    progress_callback=on_progress,
                    file_1_name=f"{file_1.original_filename} ({sheet_name_1})",
                    file_2_name=f"{file_2.original_filename} ({sheet_name_2})",
                    is_cancelled=is_cancelled,
                )
            else:
                key_f1 = pair.get("key_file_1", payload.get("key_file_1"))
                key_f2 = pair.get("key_file_2", payload.get("key_file_2"))
                if isinstance(key_f1, str): key_f1 = [key_f1]
                if isinstance(key_f2, str): key_f2 = [key_f2]

                res = run_generic_reconciliation(
                    file_1_df=df1,
                    file_2_df=df2,
                    output_path=output_path,
                    key_file_1=key_f1,
                    key_file_2=key_f2,
                    rules=pair.get("rules", payload.get("rules", [])),
                    orientation=payload.get("orientation", job.orientation),
                    include_columns_file_1=pair.get("include_columns_file_1", payload.get("include_columns_file_1", [])),
                    include_columns_file_2=pair.get("include_columns_file_2", payload.get("include_columns_file_2", [])),
                    progress_callback=on_progress,
                    file_1_name=f"{file_1.original_filename if file_1 else 'File 1'} ({sheet_name_1})",
                    file_2_name=f"{file_2.original_filename if file_2 else 'File 2'} ({sheet_name_2})",
                    is_cancelled=is_cancelled,
                )
            
            for k, v in res["summary"].items():
                overall_summary[k] = overall_summary.get(k, 0) + v
                
            ud = res["universal_data"]
            # Tag all records with the sheet pair label for unified reporting
            for category in ["exceptions", "matched_records", "missing_in_file_1", "missing_in_file_2", "field_differences"]:
                for record in ud.get(category, []):
                    record["Sheet Pair"] = pair_label
            
            all_universal_data.append(ud)

        if not all_universal_data:
            raise ValueError("No data processed for any sheet pair.")

        # Merge universal data
        merged_ud = all_universal_data[0]
        if len(all_universal_data) > 1:
            for i in range(1, len(all_universal_data)):
                ud = all_universal_data[i]
                merged_ud["statistics"] = {k: merged_ud["statistics"].get(k, 0) + ud["statistics"].get(k, 0) for k in merged_ud["statistics"]}
                if ud["overall_status"] != "PASSED":
                    merged_ud["overall_status"] = ud["overall_status"]
                for category in ["exceptions", "matched_records", "missing_in_file_1", "missing_in_file_2", "field_differences"]:
                    merged_ud[category].extend(ud.get(category, []))
                
                # Combine control checks
                for cc1, cc2 in zip(merged_ud["control_checks"], ud["control_checks"]):
                    if cc1["File 1"] != "-" and cc2["File 1"] != "-":
                        cc1["File 1"] += cc2["File 1"]
                    if cc1["File 2"] != "-" and cc2["File 2"] != "-":
                        cc1["File 2"] += cc2["File 2"]
                    if cc2["Result"] == "Exception":
                        cc1["Result"] = "Exception"
                        
            # Recompute exception summaries across pairs
            # Note: For simplicity, the detailed summary arrays might need re-aggregation, 
            # but we can rely on the reporter for the final output formatting.

        from app.utils.json_encoder import safe_json_dump
        raw_path = output_path.with_name(f"{output_path.stem}_data.json")
        with open(raw_path, "w", encoding="utf-8") as f:
            safe_json_dump(merged_ud, f)
            
        from app.reconciliation_engine.universal_reporter import generate_enterprise_report
        generate_enterprise_report(merged_ud, {}, output_path)
        summary = overall_summary

        if is_cancelled():
            raise InterruptedError("Reconciliation cancelled by user")

        stored_report = storage.save_report(output_path, job.session_id, output_name)
        
        # Also copy the raw JSON data so we can rebuild custom reports later
        raw_path = output_path.with_name(f"{output_path.stem}_data.json")
        if raw_path.exists():
            import shutil
            raw_storage_path = storage.resolve_path(stored_report.storage_path).with_name(f"{Path(stored_report.storage_path).stem}_data.json")
            shutil.copy(raw_path, raw_storage_path)
            
        from app.utils.json_encoder import safe_json_dumps

        report = Report(
            session_id=job.session_id,
            filename=output_name,
            storage_backend=stored_report.storage_backend,
            storage_path=stored_report.storage_path,
            size_bytes=stored_report.size_bytes,
            summary_json=safe_json_dumps(summary.get("statistics", {}) if "statistics" in summary else summary),
        )
        db.add(report)
        db.flush()

        job.report_id = report.id
        job.status = "completed"
        job.progress = 100
        job.completed_at = datetime.now(timezone.utc)
        append_history(db, job, "completed", "Reconciliation completed", safe_json_dumps(summary))
        db.commit()

        # Enforce maximum 20 stored records per session
        from app.services.job_service import prune_old_jobs
        try:
            prune_old_jobs(db, job.session_id, storage, max_records=20)
        except Exception:
            pass
    except InterruptedError:
        # User requested cancellation during processing
        try:
            db.rollback()
        except Exception:
            pass
        job = db.get(ReconciliationJob, job_id)
        if job and job.status != "cancelled":
            job.status = "cancelled"
            job.completed_at = datetime.now(timezone.utc)
            job.error_message = "Reconciliation cancelled by user"
            append_history(db, job, "cancelled", "Reconciliation cancelled by user")
            try:
                db.commit()
            except Exception:
                pass
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        job = db.get(ReconciliationJob, job_id)
        if job and job.status != "cancelled":
            job.status = "failed"
            job.progress = 100
            job.error_message = str(exc)
            job.completed_at = datetime.now(timezone.utc)
            append_history(db, job, "failed", str(exc))
            try:
                db.commit()
            except Exception:
                pass
    finally:
        db.close()
        try:
            if file_1_path and file_1_path.exists():
                file_1_path.unlink(missing_ok=True)
            if file_2_path and file_2_path.exists():
                file_2_path.unlink(missing_ok=True)
            if output_path and output_path.exists():
                output_path.unlink(missing_ok=True)
                raw_path = output_path.with_name(f"{output_path.stem}_data.json")
                if raw_path.exists():
                    raw_path.unlink(missing_ok=True)
        except Exception:
            pass
