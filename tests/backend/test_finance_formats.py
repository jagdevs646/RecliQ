"""Bank statements (MT940, CAMT.053, BAI2) and GSTR-2B JSON import."""
import io
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from _helpers import single_rule_plan, upload, wait_for_job
from app.core.upload_validation import UploadRejected, validate_upload
from app.main import app
from app.reconciliation_engine.engine import run_gst_reconciliation
from app.reconciliation_engine.ingestion import finance_formats as ff

FIXTURES = Path(__file__).parent / "fixtures" / "finance"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _parse(name: str, fmt: str) -> dict[str, pd.DataFrame]:
    return ff.parse_bytes(_read(name), fmt)


def _validate(name: str, data: bytes):
    return validate_upload(name, io.BytesIO(data), max_bytes=50 * 1024 * 1024, max_rows=500_000)


# ── Parsing ────────────────────────────────────────────────────────────────
def test_mt940_statement_lines_signs_narrative_and_balance_proof():
    sheets = _parse("statement.sta", ff.MT940)
    rows = sheets["Transactions"].to_dict("records")
    assert [row["Signed Amount"] for row in rows] == [500.0, -25.5, -200.0]
    assert rows[0]["Credit"] == 500.0 and rows[1]["Debit"] == 25.5
    assert rows[0]["Reference"] == "INV-1001" and rows[0]["Bank Reference"] == "B26010500001"
    assert rows[0]["Counterparty"] == "ACME PRIVATE LIMITED"  # From ?32/?33 sub-fields.
    assert "January order" in rows[0]["Narrative"]
    assert rows[1]["Reference"] == "" and rows[1]["Transaction Type"] == "Charges"  # NONREF dropped.
    assert rows[2]["Booking Date"] == "2026-01-06"  # ISO dates, never ambiguous.
    balance = sheets["Balances"].iloc[0]
    assert balance["Opening Balance"] == 1000.0 and balance["Closing Balance"] == 1274.5
    assert balance["Balance Check"] == "Balanced"


def test_camt053_splits_batch_bookings_and_keeps_pending_out_of_the_balance():
    sheets = _parse("statement.xml", ff.CAMT053)
    rows = sheets["Transactions"].to_dict("records")
    assert [row["Reference"] for row in rows[:2]] == ["INV-2001", "INV-2002"]  # One batch, two payments.
    assert [row["Amount"] for row in rows[:2]] == [600.0, 400.0]
    assert rows[1]["Counterparty"] == "Initech Pvt Ltd"  # Dbtr/Pty/Nm (newer schema).
    assert rows[2]["Reference"] == ""  # NOTPROVIDED is not a reference.
    assert rows[2]["Signed Amount"] == -285.0 and rows[2]["Transaction Code"] == ""
    assert rows[0]["Transaction Code"] == "PMNT-RCDT-BOOK"
    assert rows[3]["Status"] == "Pending"
    balance = sheets["Balances"].iloc[0]
    assert balance["Transactions"] == 3 and balance["Balance Check"] == "Balanced"


def test_bai2_continuations_funds_types_directions_and_control_total():
    sheets = _parse("statement.bai2", ff.BAI2)
    rows = sheets["Transactions"].to_dict("records")
    assert rows[0]["Narrative"] == "ACH CREDIT GLOBEX CORPORATION PAYMENT"  # 88 continuation.
    assert rows[1]["Reference"] == "INV-3002" and rows[1]["Amount"] == 500.0  # 'S' funds type skipped.
    assert [row["Debit/Credit"] for row in rows] == ["Credit", "Credit", "Debit", "Debit"]
    assert rows[3]["Transaction Type"] == "Miscellaneous fees"
    assert sheets["Balances"].iloc[0]["Balance Check"] == "Balanced"


def test_bai2_control_total_mismatch_is_reported_not_hidden():
    data = _read("statement.bai2").replace(b"49,2057500,7/", b"49,999,7/")
    check = ff.parse_bytes(data, ff.BAI2)["Balances"].iloc[0]["Balance Check"]
    assert check.startswith("Control total 999 does not equal")


def test_unbalanced_statement_says_by_how_much():
    data = _read("statement.sta").replace(b":62F:C260106EUR1274,50", b":62F:C260106EUR1300,00")
    assert ff.parse_bytes(data, ff.MT940)["Balances"].iloc[0]["Balance Check"] == "Off by 25.50"


def test_gstr2b_maps_to_the_gst_engine_columns_with_signed_credit_notes():
    frame = _parse("gstr2b.json", ff.GSTR2B)["GSTR-2B"]
    assert list(frame.columns[:10]) == [
        "GSTR", "NAME OF TRADER/FIRM/COMPANY", "INVOICE NO.", "INVOICE DATE", "TAXABLE VALUE",
        "IGST", "CGST", "SGST", "CESS", "INVOICE VALUE",
    ]
    rows = frame.to_dict("records")
    assert rows[0]["INVOICE DATE"] == "2026-01-05" and rows[0]["ITC AVAILABLE"] == "Yes"
    assert rows[1]["TAXABLE VALUE"] == 2000.0 and rows[1]["CGST"] == 180.0  # Rate-wise items summed.
    assert rows[2]["DOCUMENT TYPE"] == "Credit note" and rows[2]["INVOICE VALUE"] == -590.0
    assert rows[2]["CGST"] == 0.0 and str(rows[2]["CGST"]) == "0.0"  # No negative zero.
    assert rows[3]["SECTION"] == "Import of goods" and rows[3]["INVOICE NO."] == "BOE-991"


def test_gstr2b_reconciles_against_a_purchase_register():
    portal = _parse("gstr2b.json", ff.GSTR2B)["GSTR-2B"]
    books = pd.DataFrame([
        {"GSTR": "27AABCA1234B1Z5", "NAME OF TRADER/FIRM/COMPANY": "Acme Pvt Ltd", "INVOICE NO.": "INV-001", "INVOICE DATE": "05/01/2026",
         "TAXABLE VALUE": 1000, "IGST": 0, "CGST": 90, "SGST": 90, "CESS": 0, "INVOICE VALUE": 1180},
        {"GSTR": "27AABCA1234B1Z5", "NAME OF TRADER/FIRM/COMPANY": "Acme Pvt Ltd", "INVOICE NO.": "INV-002", "INVOICE DATE": "20/01/2026",
         "TAXABLE VALUE": 2000, "IGST": 0, "CGST": 180, "SGST": 170, "CESS": 0, "INVOICE VALUE": 2350},
    ])
    result = run_gst_reconciliation(books, portal, Path("unused.xlsx"), write_report=False)
    summary = result["summary"]
    assert summary["matched_records"] == 2  # INV-001 and INV-002 found in the portal data.
    assert summary["only_in_file_2"] == 2  # The credit note and the bill of entry are not in the books.
    assert summary["report_rows"] >= 1  # INV-002's SGST differs.


# ── Detection and upload validation ────────────────────────────────────────
def test_txt_statements_are_recognised_by_content():
    assert ff.format_for(".txt", _read("statement.sta")) == ff.MT940
    assert ff.format_for(".txt", _read("statement.bai2")) == ff.BAI2
    assert ff.format_for(".txt", b"Invoice,Amount\nA,1\n") is None  # Ordinary CSV text.


def test_upload_counts_rows_per_statement_sheet():
    validated = _validate("statement.txt", _read("statement.sta"))
    assert validated.row_counts == {"Transactions": 4, "Balances": 2}


@pytest.mark.parametrize(
    "name, content, fragment",
    [
        ("statement.xml", b"<?xml version='1.0'?><Invoice><Total>1</Total></Invoice>", "not an ISO 20022 bank statement"),
        ("statement.xml", b"<Document><BkToCstmrStmt>", "not valid XML"),
        ("return.json", b'{"hello": "world"}', "not a GSTR-2B"),
        ("return.json", b"{not json", "not valid JSON"),
        ("statement.sta", b":20:X\n:28C:1\n", "account field (:25:) is missing"),
        ("statement.bai2", b"02,X,Y,1,260101,0000,USD,2/\n", "must start with a 01"),
    ],
)
def test_unreadable_finance_files_are_refused_at_upload(name, content, fragment):
    with pytest.raises(UploadRejected) as error:
        _validate(name, content)
    assert error.value.status_code == 415 and fragment in str(error.value)


def test_xml_entities_are_refused():
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]>'
        b"<Document><BkToCstmrStmt><Stmt><Id>&b;</Id></Stmt></BkToCstmrStmt></Document>"
    )
    with pytest.raises(UploadRejected) as error:
        _validate("statement.xml", bomb)
    assert "entities or external references" in str(error.value)


def _upload_raw(client: TestClient, name: str, data: bytes) -> dict:
    response = client.post("/api/files/upload", files={"file": (name, io.BytesIO(data), "application/octet-stream")})
    assert response.status_code == 201, response.text
    return response.json()


# ── End to end ─────────────────────────────────────────────────────────────
def test_bank_statement_reconciles_against_a_ledger_through_the_api():
    with TestClient(app) as client:
        formats = {item["extension"] for item in client.get("/api/files/formats").json()["formats"]}
        assert {".sta", ".xml", ".bai2", ".json"} <= formats
        bank = _upload_raw(client, "january.sta", _read("statement.sta"))
        sheets = client.get(f"/api/files/{bank['id']}/metadata").json()["sheets"]
        assert [sheet["id"] for sheet in sheets] == ["Transactions", "Balances"]
        columns = client.get(f"/api/files/{bank['id']}/columns", params={"sheet_id": "Transactions"}).json()["columns"]
        assert "SIGNED AMOUNT" in columns and "NARRATIVE" in columns  # Headers are normalized as usual.

        ledger = upload(client, "ledger.xlsx", {"GL": [
            {"Ref": "INV-1001", "Posted": "2026-01-05", "Amount": 500.0},
            {"Ref": "RENT-01", "Posted": "2026-01-04", "Amount": -200.0},
        ]})
        plan = single_rule_plan(ledger, bank["id"], "GL", "Transactions", **{
            "matching_strategy": {
                "primary_key_source": ["Ref"], "primary_key_destination": ["Reference"],
                "matching_passes": [{
                    "type": "amount_date", "amount_source": "Amount", "amount_destination": "Signed Amount",
                    "date_source": "Posted", "date_destination": "Booking Date", "date_window_days": 3,
                }],
            },
            "reconciliation_mapping": [{"file_1_fields": ["Amount"], "file_2_fields": ["Signed Amount"]}],
        })
        job = wait_for_job(client, client.post("/api/reconciliation/generic", json=plan).json()["id"])
        summary = client.get(f"/api/reports/job/{job['id']}/summary").json()
    assert job["status"] == "completed", job
    assert summary["matched_records"] == 2  # Invoice by reference; rent by amount + date.
    assert summary["only_in_file_2"] == 1  # The bank charge has no ledger entry.
