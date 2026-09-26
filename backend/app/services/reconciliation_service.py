from __future__ import annotations

import json
from copy import deepcopy
import re
import zipfile
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
from app.schemas.reconciliation import (
    GenericReconciliationRequest,
    GSTReconciliationRequest,
    ReconciliationPlan,
    normalize_legacy_request,
)
from app.services.job_service import append_history
from app.storage import get_storage


def _file_record(db: Session, file_id: str, session_id: str) -> UploadedFile:
    record = db.query(UploadedFile).filter(UploadedFile.id == file_id, UploadedFile.session_id == session_id).first()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File not found: {file_id}")
    return record


_UNIVERSAL_RECORD_CATEGORIES = (
    "exceptions",
    "matched_records",
    "missing_in_file_1",
    "missing_in_file_2",
    "field_differences",
    "identity_resolution",
)


def _safe_report_stem(value: str, fallback: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return (stem or fallback)[:80]


def _merge_statistic_values(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        keys = set(left) | set(right)
        return {key: _merge_statistic_values(left.get(key, 0), right.get(key, 0)) for key in keys}
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left + right
    return right if right is not None else left


def _merge_rule_universal_data(rule_results: list[dict]) -> dict:
    """Aggregate only after every sheet-rule result remains independently complete."""
    merged = deepcopy(rule_results[0]["universal_data"])
    for result in rule_results[1:]:
        universal_data = result["universal_data"]
        merged["statistics"] = _merge_statistic_values(
            merged.get("statistics", {}),
            universal_data.get("statistics", {}),
        )
        if universal_data.get("overall_status") != "PASSED":
            merged["overall_status"] = universal_data["overall_status"]
        for category in _UNIVERSAL_RECORD_CATEGORIES:
            merged.setdefault(category, []).extend(universal_data.get(category, []))
        for existing, incoming in zip(merged.get("control_checks", []), universal_data.get("control_checks", [])):
            for file_key in ("File 1", "File 2"):
                if isinstance(existing.get(file_key), (int, float)) and isinstance(incoming.get(file_key), (int, float)):
                    existing[file_key] += incoming[file_key]
            if incoming.get("Result") == "Exception":
                existing["Result"] = "Exception"
    # Records already live in the merged categories; keep only rule metadata
    # here so the stored audit JSON does not hold every record twice.
    merged["execution_results"] = [
        {key: value for key, value in result.items() if key != "universal_data"}
        for result in rule_results
    ]
    return merged


_REPORT_ARCHIVE_NAME = "RecliQ_Reconciliation_Reports.zip"


def _file_stem(filename: str | None) -> str:
    return Path(filename).stem if filename else ""


def _file_pair_manifest(rule_manifest: list[dict]) -> list[dict]:
    """Summarize each file pair from its own rules, in submission order."""
    pairs: dict[str, list[dict]] = {}
    for rule in rule_manifest:
        pairs.setdefault(rule["file_pair_id"], []).append(rule)

    manifest: list[dict] = []
    for file_pair_id, rules in pairs.items():
        completed = [rule for rule in rules if rule["status"] == "completed"]
        summary: dict[str, int | float] = {}
        for rule in completed:
            for key, value in rule["summary"].items():
                if isinstance(value, (int, float)):
                    summary[key] = summary.get(key, 0) + value
        first = rules[0]
        source_stem, destination_stem = _file_stem(first["source_file"]), _file_stem(first["destination_file"])
        label = (
            first["file_pair_label"]
            or (first["report_label"] if len(rules) == 1 else "")
            or (f"{source_stem} vs {destination_stem}" if source_stem and destination_stem else file_pair_id)
        )
        manifest.append(
            {
                "file_pair_id": file_pair_id,
                "label": label,
                "source_file": first["source_file"],
                "destination_file": first["destination_file"],
                "status": "completed" if len(completed) == len(rules) else ("completed_with_errors" if completed else "failed"),
                "sheet_rule_ids": [rule["sheet_rule_id"] for rule in rules],
                "summary": summary,
                "report_filename": None,
            }
        )
    return manifest


def _label_multi_rule_report(universal_data: dict, rules: list[dict]) -> None:
    """Multi-rule workbooks name their sources by workbook, not by first sheet."""
    if len(rules) <= 1:
        return
    metadata = universal_data.setdefault("metadata", {})
    metadata["file_1_name"] = rules[0].get("source_file") or metadata.get("file_1_name")
    metadata["file_2_name"] = rules[0].get("destination_file") or metadata.get("file_2_name")
    # Keys differ per rule; tab '08 Sheet Rules' lists each rule's own setup.
    metadata["matching_keys"] = ["Match Key"]
    metadata["secondary_keys"] = []
    metadata["compared_fields"] = []


def enqueue_generic_job(
    db: Session,
    payload: GenericReconciliationRequest,
    background_tasks: BackgroundTasks,
    session_id: str,
) -> ReconciliationJob:
    try:
        plan = normalize_legacy_request(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    source_file_ids = [
        source.file_id
        for file_pair in plan.file_pairs
        for source in file_pair.source_files
    ]
    destination_file_ids = [
        source.file_id
        for file_pair in plan.file_pairs
        for source in file_pair.destination_files
    ]
    file_1_id = source_file_ids[0] if source_file_ids else None
    file_2_id = destination_file_ids[0] if destination_file_ids else None
    if not file_1_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source file 1 is required")
    if not file_2_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source file 2 is required")

    files_to_check = set(source_file_ids + destination_file_ids)

    for fid in files_to_check:
        _file_record(db, fid, session_id)

    job = ReconciliationJob(
        session_id=session_id,
        job_type="generic",
        status="queued",
        progress=0,
        orientation=plan.orientation,
        input_file_1_id=file_1_id,
        input_file_2_id=file_2_id,
        settings_json=plan.model_dump_json(),
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


from app.reconciliation_engine.ingestion import extract_file_metadata, read_table_columns, read_table_data
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
        
    if not str(orientation).lower().startswith("horizontal"):
        # Column headers only: never parse 50k data rows just to list fields.
        return [normalize_header(col) for col in read_table_columns(path, record.original_filename, sheet_id)]

    df = read_table_data(path, record.original_filename, sheet_id)
    if df.empty:
        return []
    from app.reconciliation_engine.preprocessing import transform_horizontal_dataframe
    return [normalize_header(col) for col in transform_horizontal_dataframe(df).columns]


def process_reconciliation_job_async(job_id: str) -> None:
    # FastAPI already runs sync background tasks in a thread off the event loop.
    # Calling directly avoids double-threading (submit + future.result()) which
    # caused hangs with multi-worker Uvicorn deployments.
    process_reconciliation_job(job_id)


def process_reconciliation_job(job_id: str) -> None:
    db = SessionLocal()
    settings = get_settings()
    storage = get_storage()
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

        work_dir = Path(settings.local_storage_path) / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        output_name = "GST_Reconciliation.xlsx" if job.job_type == "gst" else "Reconciliation.xlsx"
        output_path = work_dir / f"{job.id}-{output_name}"
        payload = json.loads(job.settings_json or "{}")

        if is_cancelled():
            raise InterruptedError("Reconciliation cancelled by user")

        from app.api.routes.analysis import _load_and_consolidate
        from app.schemas.reconciliation import FileSource

        if job.job_type == "generic":
            plan = ReconciliationPlan.model_validate(payload)
            execution_items = plan.execution_rules()
        else:
            # GST retains its existing, self-contained execution contract.
            execution_items = payload.get("pairs", [])
        if job.job_type == "gst" and not execution_items:
            source_files_1 = payload.get("source_files_1") or [{"file_id": payload.get("file_1_id") or job.input_file_1_id}]
            source_files_2 = payload.get("source_files_2") or [{"file_id": payload.get("file_2_id") or job.input_file_2_id}]
            sources_1 = [FileSource(**fs) if isinstance(fs, dict) else fs for fs in source_files_1 if (fs.get("file_id") if isinstance(fs, dict) else getattr(fs, "file_id", None))]
            sources_2 = [FileSource(**fs) if isinstance(fs, dict) else fs for fs in source_files_2 if (fs.get("file_id") if isinstance(fs, dict) else getattr(fs, "file_id", None))]
            if not sources_1 and job.input_file_1_id:
                sources_1 = [FileSource(file_id=job.input_file_1_id)]
            if not sources_2 and job.input_file_2_id:
                sources_2 = [FileSource(file_id=job.input_file_2_id)]
                
            # Construct a dummy pair for backwards compatibility
            execution_items = [{
                "source_file_1": sources_1[0].model_dump() if hasattr(sources_1[0], "model_dump") else (sources_1[0] if isinstance(sources_1[0], dict) else {"file_id": sources_1[0].file_id, "sheet_id": sources_1[0].sheet_id}),
                "source_file_2": sources_2[0].model_dump() if hasattr(sources_2[0], "model_dump") else (sources_2[0] if isinstance(sources_2[0], dict) else {"file_id": sources_2[0].file_id, "sheet_id": sources_2[0].sheet_id}),
                "key_file_1": payload.get("key_file_1"),
                "key_file_2": payload.get("key_file_2"),
                "rules": payload.get("rules", []),
                "include_columns_file_1": payload.get("include_columns_file_1", []),
                "include_columns_file_2": payload.get("include_columns_file_2", [])
            }]

        rule_results: list[dict] = []
        overall_summary = {
            "report_rows": 0, "only_in_file_1": 0, "only_in_file_2": 0,
            "confidence_review": 0, "source_records": 0, "destination_records": 0,
            "matched_records": 0, "fully_matched_records": 0
        }
        rule_errors: list[dict] = []
        rule_manifest: list[dict] = []
        total_rules = len(execution_items)

        # Every canonical sheet rule runs in isolation. Aggregation happens only
        # after this loop, preserving independent configuration and audit data.
        for idx, pair in enumerate(execution_items, start=1):
            file_pair_id = pair.get("file_pair_id", f"file-pair-{idx}")
            sheet_rule_id = pair.get("sheet_rule_id", f"rule-{idx}")
            report_label = pair.get("report_label") or sheet_rule_id
            source_entries_1 = pair.get("source_files_1") or [pair["source_file_1"]]
            source_entries_2 = pair.get("source_files_2") or [pair["source_file_2"]]
            sources_1 = [FileSource(**entry) if isinstance(entry, dict) else entry for entry in source_entries_1]
            sources_2 = [FileSource(**entry) if isinstance(entry, dict) else entry for entry in source_entries_2]
            key_f1, key_f2 = pair.get("key_file_1", payload.get("key_file_1")), pair.get("key_file_2", payload.get("key_file_2"))
            manifest_entry = {
                "file_pair_id": file_pair_id,
                "file_pair_label": pair.get("file_pair_label", ""),
                "sheet_rule_id": sheet_rule_id,
                "rule_index": pair.get("rule_index", idx),
                "report_label": report_label,
                "source_file": None,
                "destination_file": None,
                "source_sheets": [source.sheet_id or "default" for source in sources_1],
                "destination_sheets": [source.sheet_id or "default" for source in sources_2],
                "primary_key_source": [key_f1] if isinstance(key_f1, str) else list(key_f1 or []),
                "primary_key_destination": [key_f2] if isinstance(key_f2, str) else list(key_f2 or []),
                "secondary_conditions": pair.get("secondary_conditions", []),
                "similarity_policy": pair.get("similarity_policy", {}),
                "date_only_override": bool(pair.get("date_only_override", False)),
                "mapping_count": len(pair.get("rules", [])),
                "status": "failed",
                "error": None,
                "summary": {},
            }
            rule_manifest.append(manifest_entry)

            # Scale each rule's real engine progress into its share of the job
            # (10-90%) so the bar never restarts, and name the rule being run.
            rule_start, rule_span = 10 + (idx - 1) * 80 / total_rules, 80 / total_rules
            stage = f"{', '.join(manifest_entry['source_sheets'])} → {', '.join(manifest_entry['destination_sheets'])}"
            if pair.get("file_pair_count", 1) > 1:
                stage = f"File pair {pair['file_pair_index']} of {pair['file_pair_count']} · {stage}"
            if pair.get("rule_count", 1) > 1:
                stage = f"{stage} (rule {pair['rule_index']} of {pair['rule_count']})"

            def rule_progress(percent: int, step_msg: str, _start=rule_start, _span=rule_span, _stage=stage) -> None:
                on_progress(int(_start + _span * max(0, min(100, percent)) / 100), f"{_stage}: {step_msg}")

            try:
                s1, s2 = sources_1[0], sources_2[0]
                source_record = _file_record(db, s1.file_id, job.session_id)
                destination_record = _file_record(db, s2.file_id, job.session_id)
                manifest_entry["source_file"] = source_record.original_filename
                manifest_entry["destination_file"] = destination_record.original_filename
                if job.job_type == "generic":
                    on_progress(int(rule_start), f"Reconciling {stage}")
                df1 = _load_and_consolidate(db, job.session_id, sources_1)
                df2 = _load_and_consolidate(db, job.session_id, sources_2)
                sheet_name_1, sheet_name_2 = s1.sheet_id or "default", s2.sheet_id or "default"
                report_label = pair.get("report_label") or f"{sheet_name_1} <-> {sheet_name_2}"
                manifest_entry["report_label"] = report_label
                pair_label = f"[{report_label}]"

                if job.job_type == "gst":
                    res = run_gst_reconciliation(
                        file_1_df=df1,
                        file_2_df=df2,
                        output_path=output_path,
                        orientation=payload.get("orientation", job.orientation),
                        text_threshold=int(payload.get("text_threshold", 85)),
                        progress_callback=on_progress,
                        file_1_name=f"{source_record.original_filename} ({sheet_name_1})",
                        file_2_name=f"{destination_record.original_filename} ({sheet_name_2})",
                        is_cancelled=is_cancelled,
                        write_report=False,
                    )
                else:
                    res = run_generic_reconciliation(
                        file_1_df=df1, file_2_df=df2, output_path=output_path,
                        key_file_1=[key_f1] if isinstance(key_f1, str) else key_f1,
                        key_file_2=[key_f2] if isinstance(key_f2, str) else key_f2,
                        rules=pair.get("rules", payload.get("rules", [])), orientation=payload.get("orientation", job.orientation),
                        include_columns_file_1=pair.get("include_columns_file_1", payload.get("include_columns_file_1", [])),
                        include_columns_file_2=pair.get("include_columns_file_2", payload.get("include_columns_file_2", [])),
                        progress_callback=rule_progress, file_1_name=f"{source_record.original_filename} ({sheet_name_1})",
                        file_2_name=f"{destination_record.original_filename} ({sheet_name_2})", is_cancelled=is_cancelled,
                        write_report=False, secondary_conditions=pair.get("secondary_conditions", []),
                        similarity_policy=pair.get("similarity_policy", {}), date_only_override=bool(pair.get("date_only_override", False)),
                    )

                for k, v in res["summary"].items():
                    overall_summary[k] = overall_summary.get(k, 0) + v
                ud = res["universal_data"]
                for category in _UNIVERSAL_RECORD_CATEGORIES:
                    for record in ud.get(category, []):
                        record["Sheet Pair"], record["File Pair ID"], record["Sheet Rule ID"] = pair_label, file_pair_id, sheet_rule_id
                manifest_entry.update(status="completed", summary=res["summary"])
                rule_results.append({**manifest_entry, "universal_data": ud})
            except InterruptedError:
                raise
            except Exception as exc:
                # str(KeyError) wraps its message in quotes; report the message itself.
                message = str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)
                manifest_entry.update(status="failed", error=message)
                error = {"file_pair_id": file_pair_id, "sheet_rule_id": sheet_rule_id, "report_label": report_label, "status": "failed", "error": message}
                rule_errors.append(error)
                append_history(db, job, "processing", f"{report_label} failed: {message}")
                db.commit()

        if not rule_results:
            details = "; ".join(error["error"] for error in rule_errors) or "No data processed for any sheet pair."
            raise ValueError(details)

        file_pair_manifest = _file_pair_manifest(rule_manifest)
        merged_ud = _merge_rule_universal_data(rule_results)
        merged_ud["execution_errors"] = rule_errors
        overall_summary["failed_rules"] = len(rule_errors)
        overall_summary["completed_rules"] = len(rule_results)

        from app.utils.json_encoder import safe_json_dump
        from app.reconciliation_engine.universal_reporter import generate_enterprise_report

        on_progress(90, "Generating reconciliation report" if len(file_pair_manifest) == 1 else f"Generating {len(file_pair_manifest)} file-pair reports")

        # A file pair is a report boundary. One pair preserves the original
        # direct XLSX download; more than one pair is delivered as a ZIP.
        if job.job_type == "generic":
            merged_ud["sheet_rules"] = rule_manifest
            merged_ud["file_pairs"] = file_pair_manifest
            if len(file_pair_manifest) == 1:
                _label_multi_rule_report(merged_ud, rule_manifest)
                generate_enterprise_report(merged_ud, {}, output_path)
            else:
                archive_path = output_path.with_name(f"{job.id}-{_REPORT_ARCHIVE_NAME}")
                with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    used_names: set[str] = set()
                    for number, pair_entry in enumerate(file_pair_manifest, start=1):
                        pair_rules = [result for result in rule_results if result["file_pair_id"] == pair_entry["file_pair_id"]]
                        if not pair_rules:
                            continue  # Every rule failed; the manifest records why.
                        pair_data = _merge_rule_universal_data(pair_rules)
                        pair_data["sheet_rules"] = [rule for rule in rule_manifest if rule["file_pair_id"] == pair_entry["file_pair_id"]]
                        _label_multi_rule_report(pair_data, pair_data["sheet_rules"])
                        # Keep each pair's own figures so its workbook can be
                        # rebuilt (customized) later from the merged data.
                        merged_ud.setdefault("file_pair_reports", {})[pair_entry["file_pair_id"]] = {
                            key: pair_data.get(key)
                            for key in ("metadata", "statistics", "overall_status", "control_checks", "sheet_rules")
                        }
                        stem = _safe_report_stem(pair_entry["label"], f"Reconciliation_{number}")
                        filename = f"{stem}.xlsx"
                        suffix = 2
                        while filename.lower() in used_names:
                            filename = f"{stem}_{suffix}.xlsx"
                            suffix += 1
                        used_names.add(filename.lower())
                        pair_entry["report_filename"] = filename
                        merged_ud["file_pair_reports"][pair_entry["file_pair_id"]]["report_filename"] = filename
                        pair_path = work_dir / f"{job.id}-{number}-{filename}"
                        generate_enterprise_report(pair_data, {}, pair_path)
                        archive.write(pair_path, filename)
                        pair_path.unlink(missing_ok=True)
                output_path = archive_path
                output_name = _REPORT_ARCHIVE_NAME
            if len(file_pair_manifest) == 1:
                file_pair_manifest[0]["report_filename"] = output_name
        else:
            generate_enterprise_report(merged_ud, {}, output_path)

        raw_path = output_path.with_name(f"{output_path.stem}_data.json")
        with open(raw_path, "w", encoding="utf-8") as f:
            safe_json_dump(merged_ud, f)
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

        # The report summary doubles as the job manifest: aggregate counts at the
        # top level (legacy shape) plus per-file-pair and per-sheet-rule detail.
        report_summary = dict(summary)
        if job.job_type == "generic":
            report_summary["file_pairs"] = file_pair_manifest
            report_summary["sheet_rules"] = rule_manifest
        report = Report(
            session_id=job.session_id,
            filename=output_name,
            storage_backend=stored_report.storage_backend,
            storage_path=stored_report.storage_path,
            size_bytes=stored_report.size_bytes,
            summary_json=safe_json_dumps(report_summary),
        )
        db.add(report)
        db.flush()

        job.report_id = report.id
        job.status = "completed_with_errors" if rule_errors else "completed"
        job.progress = 100
        job.completed_at = datetime.now(timezone.utc)
        job.error_message = safe_json_dumps(rule_errors) if rule_errors else None
        completion_message = "Reconciliation completed with sheet-rule errors" if rule_errors else "Reconciliation completed"
        append_history(db, job, job.status, completion_message, safe_json_dumps(summary))
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
            # Input paths point at durable session-owned uploads. Only transient
            # work artifacts are removed after their copies have been saved.
            if output_path and output_path.exists():
                output_path.unlink(missing_ok=True)
                raw_path = output_path.with_name(f"{output_path.stem}_data.json")
                if raw_path.exists():
                    raw_path.unlink(missing_ok=True)
        except Exception:
            pass
