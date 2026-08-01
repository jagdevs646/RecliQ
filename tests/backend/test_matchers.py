import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

import pandas as pd

from app.reconciliation.matchers import compare_values, prepare_dataframe


def test_invoice_formatting_difference_scores_high():
    result = compare_values("INV-005", "inv005", "Invoice No", "Invoice_Number")
    assert result.matched
    assert result.confidence >= 95
    assert result.matcher_type == "invoice"


def test_business_synonyms_match_company_names():
    result = compare_values("ABC PVT LTD", "ABC PRIVATE LIMITED", "Name", "Company Name", "company_name")
    assert result.matched
    assert result.confidence == 100


def test_horizontal_orientation_is_transformed():
    source = pd.DataFrame(
        {
            "Field": ["Invoice", "Amount"],
            "Record1": ["INV001", 1000],
            "Record2": ["INV002", 2000],
        }
    )
    result = prepare_dataframe(source, orientation="horizontal")
    assert list(result.columns) == ["INVOICE", "AMOUNT"]
    assert len(result) == 2

