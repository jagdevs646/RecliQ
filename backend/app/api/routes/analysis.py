import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pathlib import Path

from app.api.deps import get_actor, get_session_id
from app.database.session import get_db
from app.schemas.reconciliation import AnalysisRequest, AnalysisResponse, GenericReconciliationRequest, normalize_legacy_request
from app.services.audit_service import AuditActor, record_event
from app.models.file import UploadedFile
from app.models.precheck import ReconciliationPrecheck
from app.storage import get_storage
from app.jobs.queue import enqueue_precheck
from app.reconciliation_engine.ingestion import read_table_columns, read_table_data, read_table_sample
from app.reconciliation_engine.matching.key_analyzer import SAMPLE_ROWS
from app.reconciliation_engine.presets import gst_preset
from app.reconciliation_engine.preprocessing import prepare_dataframe
from app.reconciliation_engine.schema import consolidate_dataframes, semantic_map_columns
from app.reconciliation_engine.matching.key_analyzer import analyze_keys
from app.reconciliation_engine.cache import normalize_header
from app.utils.json_encoder import safe_json_dumps


router = APIRouter(prefix="/analysis", tags=["analysis"])


def _load_single_sheet(db: Session, session_id: str, file_source) -> 'pd.DataFrame':
    """Load a single file+sheet into a DataFrame."""
    import pandas as pd
    storage = get_storage()
    fid = getattr(file_source, "file_id", None) or (file_source.get("file_id") if isinstance(file_source, dict) else None)
    if not fid:
        return pd.DataFrame()
    record = db.query(UploadedFile).filter(
        UploadedFile.id == fid,
        UploadedFile.session_id == session_id
    ).first()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File not found: {fid}")
    path = storage.resolve_path(record.storage_path)
    sheet_id = getattr(file_source, "sheet_id", None) or (file_source.get("sheet_id") if isinstance(file_source, dict) else None) or "default"
    # Key and mapping suggestions only need a sample; never wait for a full parse.
    df = read_table_sample(path, record.original_filename, sheet_id, SAMPLE_ROWS)
    if not df.empty:
        df = prepare_dataframe(df)
    return df


def _rule_columns(rule: dict, side: str) -> list[str]:
    suffix = "1" if side == "source" else "2"
    def as_list(value) -> list:
        return [value] if isinstance(value, str) else list(value or [])

    columns = as_list(rule.get(f"key_file_{suffix}"))
    columns.extend(as_list(rule.get(f"include_columns_file_{suffix}")))
    field_key = f"file_{suffix}_fields"
    columns.extend(column for mapping in rule.get("rules", []) for column in mapping.get(field_key, []))
    condition_key = "source_column" if side == "source" else "destination_column"
    columns.extend(condition.get(condition_key) for condition in rule.get("secondary_conditions", []) if condition.get(condition_key))
    pass_fields = (
        ("amount_source", "date_source", "narrative_source")
        if side == "source" else ("amount_destination", "date_destination", "narrative_destination")
    )
    columns.extend(item[field] for item in rule.get("matching_passes", []) for field in pass_fields if item.get(field))
    date_amount = rule.get("date_amount") or {}
    direction = "source" if side == "source" else "destination"
    columns.extend(date_amount.get(f"{field}_{direction}") for field in ("date", "amount", "debit", "credit", "text") if date_amount.get(f"{field}_{direction}"))
    for step in rule.get("transformations", []):
        if step.get("side", "both") not in {side, "both"}:
            continue
        if step.get("operation") == "debit_credit_to_signed":
            params = step.get("params") or {}
            columns.extend((params.get("debit_column"), params.get("credit_column")))
        else:
            columns.extend(step.get("columns") or [])
    return list(dict.fromkeys(normalize_header(column) for column in columns if column))


def _load_and_consolidate(
    db: Session,
    session_id: str,
    file_sources: list,
    *,
    needed_columns: list[str] | None = None,
    orientation: str = "vertical",
) -> 'pd.DataFrame':
    """Load and consolidate multiple file sources into one DataFrame."""
    import pandas as pd
    storage = get_storage()

    dfs_with_metadata = []

    for fs in file_sources:
        fid = getattr(fs, "file_id", None) or (fs.get("file_id") if isinstance(fs, dict) else None)
        if not fid:
            continue
        record = db.query(UploadedFile).filter(
            UploadedFile.id == fid,
            UploadedFile.session_id == session_id
        ).first()

        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File not found: {fid}")

        path = storage.resolve_path(record.storage_path)
        sheet_id = getattr(fs, "sheet_id", None) or (fs.get("sheet_id") if isinstance(fs, dict) else None) or "default"

        projected_columns = None
        if needed_columns is not None and not str(orientation).lower().startswith("horizontal"):
            raw_columns = read_table_columns(path, record.original_filename, sheet_id)
            raw_by_normalized = {normalize_header(column): column for column in raw_columns}
            projected_columns = [raw_by_normalized[column] for column in needed_columns if column in raw_by_normalized]
        df = read_table_data(path, record.original_filename, sheet_id, columns=projected_columns)
        if not df.empty:
            df = prepare_dataframe(df)
            dfs_with_metadata.append({
                "df": df,
                "filename": record.original_filename,
                "sheet_name": sheet_id
            })

    return consolidate_dataframes(dfs_with_metadata)


def _analyze_pair(df1, df2) -> dict:
    """Run key analysis and semantic mapping on a pair of DataFrames."""
    cols1 = [c for c in df1.columns if not c.startswith("__")]
    cols2 = [c for c in df2.columns if not c.startswith("__")]
    mappings = semantic_map_columns(cols1, cols2)
    key_analysis = analyze_keys(df1, df2, mappings)
    return {
        "recommended_keys_1": key_analysis["recommended_key"],
        "recommended_keys_2": key_analysis["recommended_key_destination"],
        "key_confidence": key_analysis.get("confidence", 0),
        "is_composite_key": key_analysis.get("is_composite", False),
        "key_reason": key_analysis.get("reason", ""),
        "date_columns_1": key_analysis.get("date_columns_1", []),
        "date_columns_2": key_analysis.get("date_columns_2", []),
        "recommended_mappings": mappings,
        "presets": [gst_preset(df1, df2)],
    }


@router.post("/", response_model=AnalysisResponse)
def analyze_reconciliation_files(
    payload: AnalysisRequest,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
):
    try:
        # If each source list has exactly one entry, do single-pair analysis for best results
        if len(payload.source_files_1) == 1 and len(payload.source_files_2) == 1:
            df1 = _load_single_sheet(db, session_id, payload.source_files_1[0])
            df2 = _load_single_sheet(db, session_id, payload.source_files_2[0])
        else:
            df1 = _load_and_consolidate(db, session_id, payload.source_files_1)
            df2 = _load_and_consolidate(db, session_id, payload.source_files_2)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if df1.empty or df2.empty:
        raise HTTPException(status_code=400, detail="One or both data sources are empty.")

    result = _analyze_pair(df1, df2)
    return AnalysisResponse(**result)



@router.post("/precheck", status_code=status.HTTP_202_ACCEPTED)
def precheck_reconciliation(
    payload: GenericReconciliationRequest,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> dict:
    """Queue the data-quality check for a planned run."""
    try:
        plan = normalize_legacy_request(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    task = ReconciliationPrecheck(
        session_id=session_id,
        status="queued",
        plan_json=safe_json_dumps(plan.model_dump(mode="json")),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    record_event(
        scope_id=session_id, actor=actor, action="precheck.queued", entity_type="precheck",
        entity_id=task.id, summary="Data-quality pre-check queued",
    )
    try:
        enqueue_precheck(task.id)
    except Exception:
        task.status = "failed"
        task.error_message = "The data check could not be queued. Try again in a moment."
        db.commit()
    db.refresh(task)
    return {
        "id": task.id,
        "status": task.status,
        "result": json.loads(task.result_json) if task.result_json else None,
        "error": task.error_message,
    }


@router.get("/precheck/{task_id}")
def get_precheck_status(
    task_id: str,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> dict:
    task = db.query(ReconciliationPrecheck).filter(
        ReconciliationPrecheck.id == task_id,
        ReconciliationPrecheck.session_id == session_id,
    ).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Data check not found")
    return {
        "id": task.id,
        "status": task.status,
        "result": json.loads(task.result_json) if task.result_json else None,
        "error": task.error_message,
    }
