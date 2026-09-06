from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pathlib import Path

from app.api.deps import get_session_id
from app.database.session import get_db
from app.schemas.reconciliation import AnalysisRequest, AnalysisResponse
from app.models.file import UploadedFile
from app.storage import get_storage
from app.reconciliation_engine.ingestion import read_table_data
from app.reconciliation_engine.preprocessing import prepare_dataframe
from app.reconciliation_engine.schema import consolidate_dataframes, semantic_map_columns
from app.reconciliation_engine.matching.key_analyzer import analyze_keys


router = APIRouter(prefix="/analysis", tags=["analysis"])


def _load_and_consolidate(db: Session, session_id: str, file_sources: list) -> 'pd.DataFrame':
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
        
        df = read_table_data(path, record.original_filename, sheet_id)
        if not df.empty:
            df = prepare_dataframe(df)
            dfs_with_metadata.append({
                "df": df,
                "filename": record.original_filename,
                "sheet_name": sheet_id
            })
            
    return consolidate_dataframes(dfs_with_metadata)


@router.post("/", response_model=AnalysisResponse)
def analyze_reconciliation_files(
    payload: AnalysisRequest,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
):
    try:
        df1 = _load_and_consolidate(db, session_id, payload.source_files_1)
        df2 = _load_and_consolidate(db, session_id, payload.source_files_2)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    if df1.empty or df2.empty:
        raise HTTPException(status_code=400, detail="One or both data sources are empty.")
        
    # Semantic mapping of columns from df1 -> df2
    cols1 = [c for c in df1.columns if not c.startswith("__")]
    cols2 = [c for c in df2.columns if not c.startswith("__")]
    
    mappings = semantic_map_columns(cols1, cols2)
    
    # Analyze keys
    key_analysis = analyze_keys(df1, df2)
    
    recommended_keys_1 = []
    recommended_keys_2 = []
    
    # If the key analyzer suggests a key, we need to map it back to the respective files
    # Actually, analyze_keys operates on the overlapping columns. So the name is the same in df1 and df2
    if key_analysis.get("recommended_key"):
        keys = key_analysis["recommended_key"]
        if isinstance(keys, str):
            keys = [keys]
        recommended_keys_1 = keys
        recommended_keys_2 = keys
        
    return AnalysisResponse(
        recommended_keys_1=recommended_keys_1,
        recommended_keys_2=recommended_keys_2,
        key_confidence=key_analysis.get("confidence", 0),
        is_composite_key=key_analysis.get("is_composite", False),
        recommended_mappings=mappings
    )
