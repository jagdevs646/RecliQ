import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.reconciliation import GenericReconciliationRequest, GSTReconciliationRequest, FileSource


def test_schema_model_validators():
    # Only source_files supplied
    req = GenericReconciliationRequest(
        source_files_1=[FileSource(file_id="f1", sheet_id="s1")],
        source_files_2=[FileSource(file_id="f2", sheet_id="s2")],
        key_file_1="ID",
        key_file_2="ID",
        rules=[]
    )
    assert req.file_1_id == "f1"
    assert req.file_2_id == "f2"

    # Only file_1_id / file_2_id supplied
    req_legacy = GenericReconciliationRequest(
        file_1_id="f1",
        file_2_id="f2",
        key_file_1="ID",
        key_file_2="ID",
        rules=[]
    )
    assert len(req_legacy.source_files_1) == 1
    assert req_legacy.source_files_1[0].file_id == "f1"
    assert len(req_legacy.source_files_2) == 1
    assert req_legacy.source_files_2[0].file_id == "f2"

    # GST request validator
    gst_req = GSTReconciliationRequest(
        source_files_1=[FileSource(file_id="g1")],
        source_files_2=[FileSource(file_id="g2")]
    )
    assert gst_req.file_1_id == "g1"
    assert gst_req.file_2_id == "g2"


def test_enqueue_with_source_files_only():
    with TestClient(app) as client:
        # Create dummy excel files
        df1 = pd.DataFrame({"ID": ["A1", "A2"], "Amount": [100, 200]})
        df2 = pd.DataFrame({"ID": ["A1", "A2"], "Amount": [100, 200]})

        buf1 = io.BytesIO()
        df1.to_excel(buf1, index=False)
        buf1.seek(0)

        buf2 = io.BytesIO()
        df2.to_excel(buf2, index=False)
        buf2.seek(0)

        res1 = client.post("/api/files/upload", files={"file": ("file1.xlsx", buf1, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        res2 = client.post("/api/files/upload", files={"file": ("file2.xlsx", buf2, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})

        assert res1.status_code == 201
        assert res2.status_code == 201

        f1_id = res1.json()["id"]
        f2_id = res2.json()["id"]

        # Call /api/reconciliation/generic with ONLY source_files_1 and source_files_2 (no file_1_id or file_2_id)
        payload = {
            "source_files_1": [{"file_id": f1_id, "sheet_id": "Sheet1"}],
            "source_files_2": [{"file_id": f2_id, "sheet_id": "Sheet1"}],
            "key_file_1": "ID",
            "key_file_2": "ID",
            "rules": [{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}],
            "orientation": "vertical",
            "include_columns_file_1": [],
            "include_columns_file_2": []
        }

        res_recon = client.post("/api/reconciliation/generic", json=payload)
        assert res_recon.status_code == 200
        job_data = res_recon.json()
        assert job_data["status"] in ("queued", "processing", "completed")
        assert job_data["input_file_1_id"] == f1_id
        assert job_data["input_file_2_id"] == f2_id
