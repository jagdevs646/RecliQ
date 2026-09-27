"""Shared helpers for API-level tests."""
import io
import time

import pandas as pd
from fastapi.testclient import TestClient

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def workbook_bytes(sheets: dict[str, list[dict]]) -> io.BytesIO:
    stream = io.BytesIO()
    with pd.ExcelWriter(stream, engine="openpyxl") as writer:
        for sheet_name, rows in sheets.items():
            pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name, index=False)
    stream.seek(0)
    return stream


def upload(client: TestClient, name: str, sheets: dict[str, list[dict]]) -> str:
    response = client.post("/api/files/upload", files={"file": (name, workbook_bytes(sheets), XLSX)})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def wait_for_job(client: TestClient, job_id: str, timeout: float = 30) -> dict:
    deadline = time.time() + timeout
    job = client.get(f"/api/jobs/{job_id}").json()
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"completed", "completed_with_errors", "failed", "cancelled"}:
            return job
        time.sleep(0.1)
    raise AssertionError(f"Job {job_id} did not finish: {job}")


def single_rule_plan(source_id: str, destination_id: str, source_sheet: str, destination_sheet: str, **rule) -> dict:
    """A one-pair, one-rule plan; ``rule`` supplies the rule's fields."""
    return {
        "orientation": "vertical",
        "file_pairs": [{
            "file_pair_id": "pair-1",
            "source_files": [{"file_id": source_id, "sheet_id": source_sheet}],
            "destination_files": [{"file_id": destination_id, "sheet_id": destination_sheet}],
            "sheet_rules": [{
                "sheet_rule_id": "rule-1-1",
                "source_sheets": [source_sheet],
                "destination_sheets": [destination_sheet],
                **rule,
            }],
        }],
    }


def preview(client: TestClient, job_id: str, category: str) -> list[dict]:
    rows, offset = [], 0
    while True:
        page = client.get(f"/api/reports/job/{job_id}/preview", params={"category": category, "offset": offset, "limit": 25}).json()
        rows += page["rows"]
        offset += 25
        if offset >= page["total_rows"]:
            return rows
