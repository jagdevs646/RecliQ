import sys
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
