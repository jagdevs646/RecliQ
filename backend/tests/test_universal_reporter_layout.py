from pathlib import Path

import openpyxl

from app.reconciliation_engine.universal_reporter import generate_enterprise_report


def test_report_restores_legacy_layout_without_excel_tables(tmp_path: Path) -> None:
    output = tmp_path / "reconciliation.xlsx"
    data = {
        "metadata": {
            "file_1_name": "Source.xlsx",
            "file_2_name": "Target.xlsx",
            "matching_keys": ["ID"],
            "compared_fields": ["Amount"],
        },
        "statistics": {"total_file_1": 3, "total_file_2": 3, "mismatched": 1},
        "matched_records": [{
            "MATCH KEY": "K0",
            "MATCHED KEY": "K0",
            "ROW (FILE 1)": 2,
            "ROW (FILE 2)": 2,
        }],
        "exceptions": [{
            "Primary Key": "K1",
            "Matched Key": "K1",
            "Field": "Amount",
            "File 1 Value": 10.0,
            "File 2 Value": 9.0,
            "Difference": 1.0,
            "Difference %": 0.1,
        }],
        "missing_in_file_2": [{"ID": "K2", "Description": "Source only", "ROW (FILE 1)": 3}],
        "missing_in_file_1": [{"ID": "K3", "Description": "Target only", "ROW (FILE 2)": 3}],
    }

    generate_enterprise_report(data, {}, output)
    workbook = openpyxl.load_workbook(output, data_only=False)
    summary = workbook["01 Summary"]
    differences = workbook["02 Differences"]
    checks = workbook["07 Checks"]

    assert summary["B1"].value == "Reconciliation Report"
    assert summary["B6"].value == "Records in Source.xlsx"
    assert summary["E6"].value == "Matched, no differences"
    assert summary["G6"].value == "Exact agreement rate"
    assert summary["G7"].value == 0.25
    assert summary["G7"].number_format == "0.00%"
    assert len(summary._charts) == 1
    assert summary["H11"].hyperlink.location == "'06 Matched'!A1"

    assert differences["A1"].value.startswith("Differences —")
    assert differences["A4"].value == "Key in Source.xlsx"
    assert differences["A5"].value == "K1"
    assert differences.auto_filter.ref == "A4:H5"
    assert differences.freeze_panes == "A5"
    assert differences.row_dimensions[1].height == 22
    assert differences.row_dimensions[4].height == 30
    assert differences.column_dimensions["D"].width > 0
    assert differences["A4"].font.name == "Arial"
    assert differences["A4"].fill.fgColor.rgb == "FF1F3864"

    assert checks["A1"].value == "Checks — is every record accounted for?"
    assert checks["A11"].value == "How matching was done"
    assert all(not sheet.tables for sheet in workbook.worksheets)
    workbook.close()
