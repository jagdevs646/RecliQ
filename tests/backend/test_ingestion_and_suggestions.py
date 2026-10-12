import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

import pandas as pd

from app.reconciliation_engine.ingestion import clear_table_cache, read_table_columns, read_table_data, read_table_sample
from app.reconciliation_engine.matching.indexed_matcher import detect_matcher_type
from app.reconciliation_engine.matching.key_analyzer import analyze_keys
from app.reconciliation_engine.preprocessing import prepare_dataframe


def test_sheets_are_parsed_once_and_callers_get_independent_copies(tmp_path: Path):
    path = tmp_path / "ledger.xlsx"
    pd.DataFrame({"Invoice": ["A-1", "A-2", "A-3"], "Amount": [1, 2, 3]}).to_excel(path, index=False, sheet_name="Data")

    assert read_table_columns(path, path.name, "Data") == ["Invoice", "Amount"]
    first = read_table_data(path, path.name, "Data")
    first.loc[0, "Amount"] = 999  # Mutating a result must not alter the cache.
    second = read_table_data(path, path.name, "Data")
    assert second.loc[0, "Amount"] == 1
    assert len(read_table_sample(path, path.name, "Data", 2)) == 2
    assert list(tmp_path.glob("ledger.xlsx.sheet-*.pkl"))  # Reused by other workers.

    clear_table_cache(path)
    assert not list(tmp_path.glob("ledger.xlsx.sheet-*.pkl"))


def test_long_digit_references_remain_text_when_reading_excel(tmp_path: Path):
    path = tmp_path / "long-references.xlsx"
    workbook = __import__("xlsxwriter").Workbook(str(path))
    sheet = workbook.add_worksheet("Data")
    sheet.write_row(0, 0, ["Reference", "Amount"])
    sheet.write_string(1, 0, "12345678901234567890")
    sheet.write_number(1, 1, 10)
    sheet.write_string(2, 0, "12345678901234567891")
    sheet.write_number(2, 1, 20)
    sheet.write_number(3, 0, 1.25)
    sheet.write_number(3, 1, 30)
    sheet.write_blank(4, 0, None)
    sheet.write_number(4, 1, 40)
    workbook.close()

    result = read_table_data(path, path.name, "Data")

    assert result["Reference"].iloc[:2].tolist() == ["12345678901234567890", "12345678901234567891"]


def test_long_digit_references_remain_text_when_reading_csv(tmp_path: Path):
    path = tmp_path / "long-references.csv"
    path.write_text(
        "Reference,Amount\n12345678901234567890,10\n12345678901234567891,20\n1.25,30\n,40\n",
        encoding="utf-8",
    )

    result = read_table_data(path, path.name, "default")

    assert result["Reference"].iloc[:2].tolist() == ["12345678901234567890", "12345678901234567891"]
    sample = read_table_sample(path, path.name, "default", 2)
    assert sample["Reference"].tolist() == ["12345678901234567890", "12345678901234567891"]


def test_calamine_and_openpyxl_read_edge_case_workbook_identically(tmp_path: Path):
    path = tmp_path / "reader-edges.xlsx"
    workbook = __import__("xlsxwriter").Workbook(str(path))
    sheet = workbook.add_worksheet("Data")
    date_format = workbook.add_format({"num_format": "yyyy-mm-dd"})
    datetime_format = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})
    percent_format = workbook.add_format({"num_format": "0.00%"})
    sheet.write_row(0, 0, [
        "Date", "DateTime", "LongRef", "LeadingZero", "Blank", "Mixed", "Percent", "Boolean",
        "MergedStart", "MergedEnd", "Formula",
    ])
    sheet.write_datetime(1, 0, datetime(2026, 10, 12), date_format)
    sheet.write_datetime(1, 1, datetime(2026, 10, 12, 14, 30, 45), datetime_format)
    sheet.write_string(1, 2, "12345678901234567890")
    sheet.write_string(1, 3, "0000123")
    sheet.write_blank(1, 4, None)
    sheet.write_number(1, 5, 12)
    sheet.write_number(1, 6, 0.125, percent_format)
    sheet.write_boolean(1, 7, True)
    sheet.merge_range(1, 8, 1, 9, "merged value")
    sheet.write_formula(1, 10, "=1+2", None, 3)
    sheet.write_datetime(2, 0, datetime(2026, 10, 13), date_format)
    sheet.write_datetime(2, 1, datetime(2026, 10, 13, 9, 15), datetime_format)
    sheet.write_string(2, 2, "123456789012345")
    sheet.write_string(2, 3, "000000")
    sheet.write_blank(2, 4, None)
    sheet.write_string(2, 5, "twelve")
    sheet.write_number(2, 6, 0.5, percent_format)
    sheet.write_boolean(2, 7, False)
    sheet.write_string(2, 8, "separate cell")
    sheet.write_blank(2, 9, None)
    sheet.write_formula(2, 10, "=2+3", None, 5)
    workbook.close()

    openpyxl_frame = pd.read_excel(path, sheet_name="Data", engine="openpyxl")
    calamine_frame = pd.read_excel(path, sheet_name="Data", engine="calamine")

    pd.testing.assert_frame_equal(openpyxl_frame, calamine_frame, check_dtype=True)


def test_xlsx_reader_falls_back_to_openpyxl(monkeypatch, tmp_path: Path):
    from app.reconciliation_engine.ingestion import extractor

    calls = []

    def read_excel(file_path, *, engine, **kwargs):
        calls.append(engine)
        if engine == "calamine":
            raise ValueError("unsupported workbook feature")
        return pd.DataFrame({"Value": [1]})

    monkeypatch.setattr(extractor.pd, "read_excel", read_excel)

    result = extractor._read_excel_table(tmp_path / "sample.xlsx", ".xlsx", sheet_name="Data")

    assert calls == ["calamine", "openpyxl"]
    assert result.to_dict("records") == [{"Value": 1}]


def test_key_suggestion_uses_data_not_just_names():
    """Unique amounts must not beat an invoice number, even with renamed columns."""
    source = prepare_dataframe(pd.DataFrame({
        "Invoice No": [f"INV-{i:03d}" for i in range(1, 60)],
        "Txn Date": ["2026-09-01"] * 59,
        "Amount": [i * 10 for i in range(1, 60)],
    }))
    destination = prepare_dataframe(pd.DataFrame({
        "Invoice Number": [f"INV/{i:03d}" for i in range(1, 60)],
        "Posting Date": ["01/09/2026"] * 59,
        "Net Amount": [i * 10 for i in range(1, 60)],
    }))

    result = analyze_keys(source, destination, [{"source": "INVOICE NO", "target": "INVOICE NUMBER", "confidence": "High"}])

    assert result["recommended_key"] == ["INVOICE NO"]
    assert result["recommended_key_destination"] == ["INVOICE NUMBER"]
    assert result["date_columns_1"] == ["TXN DATE"]
    assert result["date_columns_2"] == ["POSTING DATE"]


def test_short_name_hints_match_whole_words_only():
    assert detect_matcher_type([], "Invoice Date", "Invoice Date") == "date"
    assert detect_matcher_type([], "Company", "Company") == "company_name"  # Not a PAN.
    assert detect_matcher_type([], "Paid", "Paid") == "text"  # Not an "id".
    assert detect_matcher_type([], "Emp ID", "Emp ID") == "identifier"
