import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

import openpyxl
import pandas as pd
import pytest

from app.reconciliation.generic import run_generic_reconciliation
from app.reconciliation.gst import run_gst_reconciliation
from app.reconciliation_engine.universal_mapper import build_universal_data_model
from app.reconciliation_engine.universal_reporter import generate_enterprise_report


def _sample_report_data() -> dict:
    return build_universal_data_model(
        job_type="generic",
        file_1_name="Books.xlsx",
        file_2_name="Portal.xlsx",
        matching_keys=["EMP#"],
        reconciliation_results=[
            {
                "MATCH KEY": "IGS0523", "MATCHED KEY": "IGS-0523",
                "TOTAL ST (FILE 1)": 33, "TOTAL ST (FILE 2)": 32, "TOTAL ST DIFF": 1.0, "TOTAL ST STATUS": "Mismatch",
            },
        ],
        file_1_not_found=[{"EMP#": "IGS090945", "NORM_EMP#": "IGS090945", "Total ST": 40, "ROW (FILE 1)": 9,
                           "IDENTITY CLASSIFICATION": "NOT_FOUND", "MATCH EXPLANATION": "No record in the other file has this key."}],
        file_2_not_found=[{"EMP#": "IGS09091", "Total ST": 72, "ROW (FILE 2)": 7}],
        matched_records=[{"ROW (FILE 1)": 2, "ROW (FILE 2)": 2, "MATCH KEY": "IGS1344", "MATCHED KEY": "IGS1344",
                          "GROUP CLASSIFICATION": "One-to-One Match"}],
        total_file_1=3,
        total_file_2=3,
        compared_fields=["TOTAL ST ↔ TOTAL ST"],
    )


def test_report_is_plain_and_self_explanatory(tmp_path: Path):
    output_path = tmp_path / "generated_report.xlsx"
    generate_enterprise_report(_sample_report_data(), {}, output_path)
    wb = openpyxl.load_workbook(output_path)

    assert wb.sheetnames == [
        "01 Summary", "02 Differences", "03 Only in Books", "04 Only in Portal", "06 Matched", "07 Checks",
    ]
    for ws in wb.worksheets:
        assert ws.views.sheetView[0].showGridLines is False

    summary = wb["01 Summary"]
    assert summary["B1"].value == "Reconciliation Report"
    assert "Books.xlsx" in summary["B2"].value and "Portal.xlsx" in summary["B2"].value
    # Stored values only: removing a tab can never break a summary figure.
    assert not any(isinstance(cell.value, str) and cell.value.startswith("=") for row in summary.iter_rows() for cell in row)
    results = {row[1].value: row[2].value for row in summary.iter_rows(min_row=11, max_row=14)}
    assert results == {"Matched – all fields agree": 1, "Matched – values differ": 1,
                       "Only in Books.xlsx": 1, "Only in Portal.xlsx": 1}
    assert summary["H12"].hyperlink.location == "'02 Differences'!A1"
    # One chart, anchored right of the content columns (B:H), so it covers nothing.
    assert len(summary._charts) == 1
    assert summary._charts[0].anchor._from.col >= 9

    differences = wb["02 Differences"]
    headers = [cell.value for cell in differences[4]]
    assert "Exception ID" not in headers
    assert headers[:3] == ["Key in Books.xlsx", "Key in Portal.xlsx", "Field"]
    assert [cell.value for cell in differences[5]][:6] == ["IGS0523", "IGS-0523", "TOTAL ST", 33, 32, 1]

    only_books = [cell.value for cell in wb["03 Only in Books"][4]]
    assert only_books == ["EMP#", "Total ST", "Result", "Why it was not matched", "Row in Books.xlsx"]

    checks = wb["07 Checks"]
    assert checks["A9"].value.startswith("Records accounted for")
    assert (checks["B9"].value, checks["C9"].value, checks["D9"].value) == (3, 3, "Pass")


def test_match_review_lists_only_records_to_confirm(tmp_path: Path):
    output_path = tmp_path / "identity_report.xlsx"
    data = build_universal_data_model(
        job_type="generic",
        file_1_name="Source.xlsx",
        file_2_name="Destination.xlsx",
        matching_keys=["Customer"],
        reconciliation_results=[],
        file_1_not_found=[],
        file_2_not_found=[],
        matched_records=[],
        total_file_1=3,
        total_file_2=3,
        identity_resolution=[
            {"ROW (FILE 1)": 2, "IDENTITY CLASSIFICATION": "EXCEPTION_MATCH", "MATCH KEY": "Alpah Ltd", "CANDIDATE KEY": "Alpha Ltd",
             "MATCH EXPLANATION": "Key differs slightly, but every secondary key matched.", "MATCH CONFIDENCE": "91%"},
            {"ROW (FILE 1)": 3, "IDENTITY CLASSIFICATION": "AMBIGUOUS_MATCH", "MATCH KEY": "Beta", "CANDIDATE KEY": "",
             "MATCH EXPLANATION": "Several records in the other file have a similar key.", "MATCH CONFIDENCE": "0%"},
            {"ROW (FILE 1)": 4, "IDENTITY CLASSIFICATION": "EXACT_MATCH", "MATCH KEY": "Gamma", "CANDIDATE KEY": "Gamma",
             "MATCH EXPLANATION": "Key matched.", "MATCH CONFIDENCE": "100%"},
        ],
    )

    generate_enterprise_report(data, {}, output_path)
    worksheet = openpyxl.load_workbook(output_path)["05 Match Review"]
    rows = list(worksheet.iter_rows(min_row=5, values_only=True))
    assert [row[0] for row in rows] == ["Matched by secondary keys", "Several possible matches"]
    assert rows[0][1:3] == ("Alpah Ltd", "Alpha Ltd")
    assert "MatchReviewTbl" in [table.displayName for table in worksheet.tables.values()]


def test_generic_reconciliation_end_to_end(tmp_path: Path):
    file1 = tmp_path / "ledger.xlsx"
    file2 = tmp_path / "bank.xlsx"
    output = tmp_path / "Reconciliation_Report.xlsx"

    pd.DataFrame([
        {"EmpId": "E101", "Name": "Alice Smith", "Hours": 40},
        {"EmpId": "E102", "Name": "Bob Jones", "Hours": 35},
        {"EmpId": "E103", "Name": "Charlie Brown", "Hours": 20},
    ]).to_excel(file1, index=False)

    pd.DataFrame([
        {"EmpId": "E101", "Name": "Alice Smith", "Hours": 40},
        {"EmpId": "E102", "Name": "Bob Jones", "Hours": 38}, # Mismatched hours
        {"EmpId": "E104", "Name": "David Clark", "Hours": 25}, # Missing in file 1
    ]).to_excel(file2, index=False)

    summary = run_generic_reconciliation(
        file1,
        file2,
        output,
        key_file_1="EmpId",
        key_file_2="EmpId",
        rules=[
            {"file_1_fields": ["Hours"], "file_2_fields": ["Hours"]},
            {"file_1_fields": ["Name"], "file_2_fields": ["Name"]},
        ],
        file_1_name="ledger.xlsx",
        file_2_name="bank.xlsx",
    )

    assert output.exists()
    wb = openpyxl.load_workbook(output)
    assert "01 Summary" in wb.sheetnames
    assert "02 Differences" in wb.sheetnames
    assert "06 Matched" in wb.sheetnames
    assert "03 Only in ledger" in wb.sheetnames
    assert "04 Only in bank" in wb.sheetnames
    assert "07 Checks" in wb.sheetnames
