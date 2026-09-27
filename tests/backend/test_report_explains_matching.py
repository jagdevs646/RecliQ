"""The Excel report names the pass, normalization and preparation used."""
from pathlib import Path

import openpyxl
import pandas as pd

from app.reconciliation_engine.engine import run_generic_reconciliation
from app.reconciliation_engine.preprocessing import prepare_dataframe
from app.reconciliation_engine.universal_reporter import generate_enterprise_report


def _cells(sheet) -> list[str]:
    return [str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value is not None]


def test_report_shows_passes_normalization_and_transformations(tmp_path: Path):
    source = prepare_dataframe(pd.DataFrame([
        {"Ref": "R1", "Vendor": "ABC Pvt Ltd", "Date": "01/09/2026", "Amount": 500},
        {"Ref": "R2", "Vendor": "XYZ Co", "Date": "05/09/2026", "Amount": 750},
    ]))
    destination = prepare_dataframe(pd.DataFrame([
        {"Ref": "R1", "Vendor": "ABC PRIVATE LIMITED", "Date": "01/09/2026", "Amount": -500},
        {"Ref": "B-9", "Vendor": "XYZ Company", "Date": "06/09/2026", "Amount": -750},
    ]))
    result = run_generic_reconciliation(
        source, destination, tmp_path / "unused.xlsx",
        key_file_1=["Ref"], key_file_2=["Ref"],
        rules=[{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}],
        secondary_conditions=[{"source_column": "Vendor", "destination_column": "Vendor", "comparison_method": "exact_text"}],
        transformations=[{"operation": "invert_sign", "side": "destination", "columns": ["Amount"]}],
        matching_passes=[{"type": "amount_date", "amount_source": "Amount", "amount_destination": "Amount",
                          "date_source": "Date", "date_destination": "Date", "date_window_days": 2}],
        write_report=False,
    )
    data = result["universal_data"]
    data["sheet_rules"] = [{
        "sheet_rule_id": "rule-1", "status": "completed", "source_sheets": ["Books"], "destination_sheets": ["Bank"],
        "primary_key_source": ["REF"], "primary_key_destination": ["REF"], "secondary_conditions": [],
        "transformations": [{"operation": "invert_sign", "side": "destination", "columns": ["Amount"], "params": {}}],
        "matching_passes": [{"type": "amount_date", "date_source": "DATE", "date_window_days": 2}],
        "date_format": "day_first", "summary": result["summary"],
    }]
    output = tmp_path / "report.xlsx"
    generate_enterprise_report(data, {}, output)

    workbook = openpyxl.load_workbook(output)
    summary_text = " ".join(_cells(workbook["01 Summary"]))
    assert "Matching passes (in order)" in summary_text and "Pass 2: Amount + date ±2 days (1 matched)" in summary_text
    assert "Name normalization used" in summary_text and "Pvt → Private" in summary_text
    assert "Invert sign: Amount" in summary_text

    review_text = " ".join(_cells(workbook["05 Match Review"]))
    assert "Matched by amount + date ±2 days" in review_text

    rules_text = " ".join(_cells(workbook["08 Sheet Rules"]))
    assert "Invert sign: Amount (destination)" in rules_text
    assert "Pass 2: Amount + date ±2 days" in rules_text
