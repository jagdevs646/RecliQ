import io
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app


def _workbook_bytes(sheets: dict[str, list[dict]]) -> io.BytesIO:
    stream = io.BytesIO()
    with pd.ExcelWriter(stream, engine="openpyxl") as writer:
        for sheet_name, rows in sheets.items():
            pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name, index=False)
    stream.seek(0)
    return stream


def test_multiple_file_pairs_download_as_zip_with_independent_workbooks():
    with TestClient(app) as client:
        source = _workbook_bytes({
            "North": [{"Invoice": "N-001", "Amount": 10}],
            "South": [{"Reference": "S-001", "Amount": 20}],
        })
        destination = _workbook_bytes({
            "North ledger": [{"Document": "N001", "Net": 10}],
            "South ledger": [{"Bank Ref": "S001", "Credit": 20}],
        })
        uploaded_source = client.post("/api/files/upload", files={"file": ("North_South.xlsx", source, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        uploaded_destination = client.post("/api/files/upload", files={"file": ("Ledger.xlsx", destination, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        assert uploaded_source.status_code == 201
        assert uploaded_destination.status_code == 201

        source_id = uploaded_source.json()["id"]
        destination_id = uploaded_destination.json()["id"]
        payload = {
            "orientation": "vertical",
            "file_pairs": [
                {
                    "file_pair_id": "north",
                    "source_files": [{"file_id": source_id, "sheet_id": "North"}],
                    "destination_files": [{"file_id": destination_id, "sheet_id": "North ledger"}],
                    "sheet_rules": [{
                        "sheet_rule_id": "north-rule",
                        "source_sheets": ["North"],
                        "destination_sheets": ["North ledger"],
                        "matching_strategy": {"primary_key_source": ["Invoice"], "primary_key_destination": ["Document"]},
                        "reconciliation_mapping": [{"file_1_fields": ["Amount"], "file_2_fields": ["Net"]}],
                        "report_label": "North report",
                    }],
                },
                {
                    "file_pair_id": "south",
                    "source_files": [{"file_id": source_id, "sheet_id": "South"}],
                    "destination_files": [{"file_id": destination_id, "sheet_id": "South ledger"}],
                    "sheet_rules": [{
                        "sheet_rule_id": "south-rule",
                        "source_sheets": ["South"],
                        "destination_sheets": ["South ledger"],
                        "matching_strategy": {"primary_key_source": ["Reference"], "primary_key_destination": ["Bank Ref"]},
                        "reconciliation_mapping": [{"file_1_fields": ["Amount"], "file_2_fields": ["Credit"]}],
                        "report_label": "South report",
                    }],
                },
            ],
        }
        started = client.post("/api/reconciliation/generic", json=payload)
        assert started.status_code == 200
        job = started.json()
        for _ in range(40):
            job = client.get(f"/api/jobs/{job['id']}").json()
            if job["status"] in {"completed", "failed"}:
                break
            time.sleep(0.1)
        assert job["status"] == "completed", job.get("error_message")

        report = client.get(f"/api/reports/job/{job['id']}/download")
        assert report.status_code == 200
        assert report.headers["content-type"].startswith("application/zip")
        assert len(report.content) > 100
        with zipfile.ZipFile(io.BytesIO(report.content)) as archive:
            assert archive.namelist() == ["North_report.xlsx", "South_report.xlsx"]
