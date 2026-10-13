"""The GST preset (ADR-0001): detection by values, and the rule it fills in."""
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from _helpers import upload
from app.main import app
from app.reconciliation_engine.engine import run_generic_reconciliation
from app.reconciliation_engine.ingestion.finance_formats import GSTR2B_COLUMNS
from app.reconciliation_engine.presets import _GSTIN_CHARS, gst_preset, gstin_is_valid
from app.reconciliation_engine.preprocessing import prepare_dataframe


def _gstin(first_14: str) -> str:
    """A GSTIN with its correct check digit."""
    total = 0
    for position, char in enumerate(first_14):
        product = _GSTIN_CHARS.index(char) * (2 if position % 2 else 1)
        total += product // 36 + product % 36
    return first_14 + _GSTIN_CHARS[(36 - total % 36) % 36]


ACME, BETA = _gstin("27AABCA1234B1Z"), _gstin("29AAGCB9876C1Z")


def _register_row(gstin: str, invoice: str, name: str = "Acme Pvt Ltd", taxable: float = 1000, cgst: float = 90, **extra) -> dict:
    """A purchase register row with the headings accounting software uses."""
    return {"Supplier GSTIN": gstin, "Party Name": name, "Invoice Number": invoice, "Invoice Date": "05/01/2026",
            "State": "Maharashtra", "Taxable Amount": taxable, "Integrated Tax": 0, "Central Tax": cgst,
            "State/UT Tax": cgst, "Cess": 0, "Invoice Amount": taxable + 2 * cgst, **extra}


def _portal_row(gstin: str, invoice: str, name: str = "Acme Private Limited", taxable: float = 1000, cgst: float = 90,
                document_type: str = "Invoice") -> dict:
    """A row as the GSTR-2B reader writes it."""
    row = dict.fromkeys(GSTR2B_COLUMNS, "")
    row.update({"GSTR": gstin, "NAME OF TRADER/FIRM/COMPANY": name, "INVOICE NO.": invoice, "INVOICE DATE": "05/01/2026",
                "TAXABLE VALUE": taxable, "IGST": 0, "CGST": cgst, "SGST": cgst, "CESS": 0,
                "INVOICE VALUE": taxable + 2 * cgst, "DOCUMENT TYPE": document_type})
    return row


def _frames(register: list[dict], portal: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    return prepare_dataframe(pd.DataFrame(register)), prepare_dataframe(pd.DataFrame(portal))


def _run(preset: dict, df1: pd.DataFrame, df2: pd.DataFrame) -> dict:
    rule = preset["rule"]
    strategy = rule["matching_strategy"]
    return run_generic_reconciliation(
        df1, df2, Path("unused.xlsx"), write_report=False,
        key_file_1=strategy["primary_key_source"], key_file_2=strategy["primary_key_destination"],
        secondary_conditions=strategy["secondary_conditions"], rules=rule["reconciliation_mapping"],
        tolerances=rule["tolerances"], date_dayfirst=rule["date_format"] == "day_first",
    )


def test_gstin_check_digit():
    assert gstin_is_valid("27AAPFU0939F1ZV")  # A published sample GSTIN.
    assert not gstin_is_valid("27AAPFU0939F1ZA")  # Same shape, wrong check digit.
    assert not gstin_is_valid("IMPORT")


def test_a_register_against_gstr2b_is_certain_and_every_column_finds_its_role():
    df1, df2 = _frames([_register_row(ACME, "INV-001")], [_portal_row(ACME, "INV-001"), _portal_row("IMPORT", "BOE-1")])
    preset = gst_preset(df1, df2)

    assert preset["status"] == "certain"  # One GSTR-2B row is an import, with no GSTIN.
    strategy = preset["rule"]["matching_strategy"]
    assert strategy["primary_key_source"] == ["SUPPLIER GSTIN", "INVOICE NUMBER"]
    assert strategy["primary_key_destination"] == ["GSTR", "INVOICE NO."]
    assert strategy["secondary_conditions"] == [{"source_column": "SUPPLIER GSTIN", "destination_column": "GSTR", "comparison_method": "exact_text"}]
    pairs = {mapping["file_1_fields"][0]: mapping["file_2_fields"][0] for mapping in preset["rule"]["reconciliation_mapping"]}
    assert pairs == {"PARTY NAME": "NAME OF TRADER/FIRM/COMPANY", "INVOICE DATE": "INVOICE DATE", "TAXABLE AMOUNT": "TAXABLE VALUE",
                     "INTEGRATED TAX": "IGST", "CENTRAL TAX": "CGST", "STATE/UT TAX": "SGST", "CESS": "CESS",
                     "INVOICE AMOUNT": "INVOICE VALUE"}  # The supplier's "State" is not a tax.


def test_two_registers_with_gstins_are_likely():
    df1, df2 = _frames([_register_row(ACME, "INV-001")], [_register_row(ACME, "INV-001")])
    assert gst_preset(df1, df2)["status"] == "likely"


def test_headings_alone_never_suggest_gst():
    fake = [_register_row("27AABCA1234B1Z5", "INV-001")]  # GST-shaped, wrong check digit.
    df1, df2 = _frames(fake, fake)
    preset = gst_preset(df1, df2)
    assert preset["status"] == "available"  # Offered if the user picks it, not suggested.
    assert preset["rule"] is not None


def test_missing_columns_make_the_preset_unavailable_and_say_which():
    register = [{key: value for key, value in _register_row(ACME, "INV-001").items() if key != "Taxable Amount"}]
    df1, df2 = _frames(register, [_portal_row(ACME, "INV-001")])
    preset = gst_preset(df1, df2)
    assert preset["status"] == "unavailable" and preset["rule"] is None
    assert preset["missing_1"] == ["taxable_value"] and preset["missing_2"] == []


def test_document_type_keeps_a_credit_note_from_pairing_with_an_invoice():
    register = [_register_row(ACME, "N-01", **{"Document Type": "Credit Note"})]
    portal = [_portal_row(ACME, "N-01", document_type="Invoice"), _portal_row(ACME, "N-01", document_type="Credit note")]
    df1, df2 = _frames(register, portal)
    preset = gst_preset(df1, df2)
    assert preset["rule"]["matching_strategy"]["primary_key_source"] == ["SUPPLIER GSTIN", "INVOICE NUMBER", "DOCUMENT TYPE"]

    summary = _run(preset, df1, df2)["summary"]
    assert summary["matched_records"] == 1 and summary["only_in_file_2"] == 1  # The invoice stays unpaired.


def test_document_types_in_other_words_stay_out_of_the_key():
    register = [_register_row(ACME, "INV-001", **{"Voucher Type": "Purchase"})]
    df1, df2 = _frames(register, [_portal_row(ACME, "INV-001")])
    preset = gst_preset(df1, df2)
    assert preset["rule"]["matching_strategy"]["primary_key_source"] == ["SUPPLIER GSTIN", "INVOICE NUMBER"]
    assert any("different words" in note for note in preset["notes"])


def test_the_preset_reconciles_like_the_owner_decided():
    register = [
        _register_row(ACME, "INV-001", name="Sharma Steel Traders"),       # Name 97% similar: matched, with a note.
        _register_row(ACME, "INV-002", taxable=600, cgst=54),              # Two lines of one invoice ...
        _register_row(ACME, "INV-002", taxable=400, cgst=36),              # ... added up.
        _register_row(ACME, "INV-003", cgst=90.5),                         # 0.50 rupees off: within ±1.
        _register_row(BETA, "INV-004", cgst=95),                           # 5 rupees off: a difference.
        _register_row(ACME, "INV-005"),                                    # Not in GSTR-2B.
    ]
    portal = [
        _portal_row(ACME, "INV-001", name="Sharma Steels Traders"),
        _portal_row(ACME, "INV-002"),
        _portal_row(ACME, "INV-003"),
        _portal_row(BETA, "INV-004"),
        _portal_row(BETA, "INV-006"),                                      # Not in the register.
    ]
    df1, df2 = _frames(register, portal)
    preset = gst_preset(df1, df2)
    assert preset["status"] == "certain"
    result = _run(preset, df1, df2)

    summary = result["summary"]
    assert (summary["matched_records"], summary["only_in_file_1"], summary["only_in_file_2"]) == (4, 1, 1)
    differences = {(line["Primary Key"], line["Field"]) for line in result["universal_data"]["exceptions"]}
    assert {field for key, field in differences} == {"CENTRAL TAX", "STATE/UT TAX", "INVOICE AMOUNT"}
    assert all(key.endswith("INV-004") for key, _ in differences)
    notes = {record["COMPOSITE MATCH KEY"]: record.get("WITHIN TOLERANCE", "") for record in result["universal_data"]["matched_records"]}
    assert "97% similar" in next(note for key, note in notes.items() if str(key).endswith("INV-001"))
    assert "within ±1" in next(note for key, note in notes.items() if str(key).endswith("INV-003"))


def test_the_analysis_step_returns_the_preset():
    with TestClient(app) as client:
        register_id = upload(client, "register.xlsx", {"Purchases": [_register_row(ACME, "INV-001")]})
        portal_id = upload(client, "portal.xlsx", {"GSTR-2B": [_portal_row(ACME, "INV-001")]})
        response = client.post("/api/analysis/", json={
            "source_files_1": [{"file_id": register_id, "sheet_id": "Purchases"}],
            "source_files_2": [{"file_id": portal_id, "sheet_id": "GSTR-2B"}],
        })
    assert response.status_code == 200, response.text
    [preset] = response.json()["presets"]
    assert preset["id"] == "gst" and preset["status"] == "certain"
