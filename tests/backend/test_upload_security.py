"""Uploads are validated by content, size and rows; .xls works end to end."""
import io

import pytest
import xlwt
from fastapi.testclient import TestClient

from _helpers import XLSX, single_rule_plan, upload, wait_for_job, workbook_bytes
from app.core.upload_validation import SUPPORTED_FORMATS, UploadRejected, validate_upload
from app.main import app

MB = 1024 * 1024


def _xls_bytes(rows: list[list]) -> io.BytesIO:
    book = xlwt.Workbook()
    sheet = book.add_sheet("Ledger")
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            sheet.write(r, c, value)
    stream = io.BytesIO()
    book.save(stream)
    stream.seek(0)
    return stream


def test_supported_formats_are_truthful():
    assert set(SUPPORTED_FORMATS) == {
        ".xlsx", ".xls", ".csv", ".tsv", ".txt", ".pdf", ".docx",
        # Bank statements and GST returns (see test_finance_formats.py).
        ".sta", ".mt940", ".940", ".bai", ".bai2", ".xml", ".json",
    }
    with TestClient(app) as client:
        formats = client.get("/api/files/formats").json()
    extensions = {item["extension"] for item in formats["formats"]}
    assert ".doc" not in extensions and ".md" not in extensions
    assert formats["max_upload_mb"] > 0 and formats["max_rows_per_sheet"] > 0


@pytest.mark.parametrize(
    "name, content, status, fragment",
    [
        ("ledger.xlsx", b"Invoice,Amount\nA,1\n", 415, "not an Excel workbook"),
        ("ledger.csv", b"PK\x03\x04binary", 415, "binary data"),
        ("ledger.csv", b"", 400, "empty"),
        ("ledger.doc", b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1", 415, "Unsupported file format"),
        ("notes.md", b"# hi", 415, "Unsupported file format"),
        ("report.pdf", b"not a pdf", 415, "not a PDF"),
        ("ledger.xls", workbook_bytes({"S": [{"A": 1}]}).getvalue(), 415, "Rename it to .xlsx"),
        ("ledger.xlsx", b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" + b"\x00" * 600, 415, "password-protected"),
    ],
)
def test_content_must_match_the_extension(name, content, status, fragment):
    with TestClient(app) as client:
        response = client.post("/api/files/upload", files={"file": (name, io.BytesIO(content), "application/octet-stream")})
    assert response.status_code == status
    assert fragment in response.json()["detail"]


def test_size_and_row_limits_are_enforced():
    csv = io.BytesIO(b"Invoice,Amount\n" + b"".join(f"INV-{i},{i}\n".encode() for i in range(20)))
    with pytest.raises(UploadRejected) as too_many_rows:
        validate_upload("big.csv", csv, max_bytes=10 * MB, max_rows=10)
    assert too_many_rows.value.status_code == 413 and "20 data rows" in str(too_many_rows.value)

    with pytest.raises(UploadRejected) as too_big:
        validate_upload("big.csv", io.BytesIO(b"a,b\n" * 1000), max_bytes=1000, max_rows=10_000)
    assert too_big.value.status_code == 413

    workbook = workbook_bytes({"Data": [{"Invoice": f"I{i}"} for i in range(30)]})
    with pytest.raises(UploadRejected):
        validate_upload("rows.xlsx", workbook, max_bytes=10 * MB, max_rows=5)
    validated = validate_upload("rows.xlsx", workbook, max_bytes=10 * MB, max_rows=50)
    assert validated.row_counts == {"Data": 31} and validated.content_type == XLSX


def test_oversized_request_is_refused_before_it_is_read():
    settings_limit = 50 * MB + 1024 * 1024
    with TestClient(app) as client:
        response = client.post(
            "/api/files/upload",
            content=b"x",
            headers={"content-type": "multipart/form-data; boundary=x", "content-length": str(settings_limit + 1)},
        )
    assert response.status_code == 413


def test_xls_workbooks_reconcile_end_to_end():
    with TestClient(app) as client:
        response = client.post(
            "/api/files/upload",
            files={"file": ("legacy.xls", _xls_bytes([["Invoice", "Amount"], ["INV-1", 100], ["INV-2", 200]]), "application/vnd.ms-excel")},
        )
        assert response.status_code == 201, response.text
        assert response.json()["content_type"] == "application/vnd.ms-excel"
        xls_id = response.json()["id"]
        assert [sheet["id"] for sheet in client.get(f"/api/files/{xls_id}/metadata").json()["sheets"]] == ["Ledger"]
        assert client.get(f"/api/files/{xls_id}/columns", params={"sheet_id": "Ledger"}).json()["columns"] == ["INVOICE", "AMOUNT"]

        other = upload(client, "books.xlsx", {"Books": [{"Invoice": "INV/1", "Amount": 100}, {"Invoice": "INV/2", "Amount": 250}]})
        plan = single_rule_plan(
            xls_id, other, "Ledger", "Books",
            matching_strategy={"primary_key_source": ["Invoice"], "primary_key_destination": ["Invoice"]},
            reconciliation_mapping=[{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}],
        )
        job = wait_for_job(client, client.post("/api/reconciliation/generic", json=plan).json()["id"])
        assert job["status"] == "completed", job["error_message"]
        summary = client.get(f"/api/reports/job/{job['id']}/summary").json()
    assert summary["exact_matches"] == 2
    assert summary["field_discrepancies"] == 1  # INV-2: 200 vs 250.
