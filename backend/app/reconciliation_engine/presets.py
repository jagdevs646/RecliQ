"""Ready-made setups ("presets") that fill in a general sheet rule.

The GST preset reconciles a purchase register against GSTR-2B (ADR-0001). It
is a setup of the general engine, not a separate engine: the user sees every
rule it fills in and can change any of them.

Detection reads the values, never the headings alone. GST files are suggested
when, in each file, at least 80% of the filled values of one column are valid
GSTINs (check digit included), and the file has an invoice number, a taxable
value and at least one tax column. A file read from GSTR-2B JSON is a certain
signal. Headings only name the role of each column (invoice number, IGST and
so on), so the preset also works when the user picks it by hand.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from app.reconciliation_engine.cache import normalize_text
from app.reconciliation_engine.ingestion.finance_formats import GSTR2B_COLUMNS
from app.reconciliation_engine.matching.indexed_matcher import compact_identifier, non_empty_values

GST_PRESET_ID = "gst"
GST_PRESET_LABEL = "GST: purchase register vs GSTR-2B"
GST_NAME_SIMILARITY = 85      # Supplier names at least this similar count as matched, with a note.
GST_AMOUNT_TOLERANCE = 1.0    # Rupees accepted on each amount, with a note.
_GSTIN_SHARE = 0.8

_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_SHAPE = re.compile(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]")

# The amount columns, in report order; tax heads are told apart by name.
AMOUNT_ROLES = ("taxable_value", "igst", "cgst", "sgst", "cess", "invoice_value")
TAX_ROLES = ("igst", "cgst", "sgst")
_ROLE_PATTERNS: dict[str, re.Pattern] = {
    "invoice_number": re.compile(r"\b(?:invoice|inv|bill|document|doc)\b.*\b(?:no|number|num)\b|^invoice$"),
    "invoice_date": re.compile(r"\b(?:invoice|inv|bill|document|doc)\b.*\bdate\b|^date$"),
    "supplier_name": re.compile(r"\bname\b|\btrader\b|\bsupplier\b|\bparty\b|\bvendor\b"),
    "taxable_value": re.compile(r"\btaxable\b|\bassessable\b"),
    # "Integrated Tax", "Central Tax", "State/UT Tax" as the GST portal names
    # them; a plain "State" column is the supplier's state, not a tax.
    "igst": re.compile(r"\bigst\b|\bintegrated tax\b"),
    "cgst": re.compile(r"\bcgst\b|\bcentral tax\b"),
    "sgst": re.compile(r"\b(?:sgst|utgst)\b|\bstate (?:ut )?tax\b"),
    "cess": re.compile(r"\bcess\b"),
    "invoice_value": re.compile(r"\b(?:invoice|inv|bill|total)\b.*\b(?:value|amount|amt|total)\b"),
    "document_type": re.compile(r"\b(?:document|doc|voucher|note)\b.*\btype\b"),
}
# Headings that look like a role but are not the invoice's own value.
_NOT_A_ROLE = re.compile(r"\boriginal\b|\bfiling\b|\breturn period\b|\bplace\b|\breason\b|\bavailable\b|\bunavailable\b")
_DOCUMENT_TYPES = {"invoice", "credit note", "debit note"}


def gstin_is_valid(value: object) -> bool:
    """A GSTIN of the right shape whose 15th character is its check digit."""
    code = compact_identifier(value).upper()
    if not _GSTIN_SHAPE.fullmatch(code):
        return False
    total = 0
    for position, char in enumerate(code[:14]):
        product = _GSTIN_CHARS.index(char) * (2 if position % 2 else 1)
        total += product // 36 + product % 36
    return code[14] == _GSTIN_CHARS[(36 - total % 36) % 36]


@dataclass
class _FileRoles:
    columns: dict[str, str] = field(default_factory=dict)   # role -> column
    gstin_by_values: bool = False
    is_gstr2b: bool = False

    def missing(self) -> list[str]:
        needed = ["gstin", "invoice_number", "taxable_value"]
        missing = [role for role in needed if role not in self.columns]
        if not any(role in self.columns for role in TAX_ROLES):
            missing.append("tax (IGST, CGST or SGST)")
        return missing


def _gstin_share(series: pd.Series) -> float:
    values = non_empty_values(series.tolist(), limit=200)
    return sum(gstin_is_valid(value) for value in values) / len(values) if values else 0.0


def _find_roles(df: pd.DataFrame) -> _FileRoles:
    roles = _FileRoles(is_gstr2b=set(GSTR2B_COLUMNS) <= set(df.columns))
    columns = [column for column in df.columns if not str(column).startswith("__")]

    # GSTIN: the column whose values are GSTINs; a GSTIN heading only breaks ties.
    shares = {column: _gstin_share(df[column]) for column in columns}
    best = max(shares, key=lambda column: (shares[column], bool(re.search(r"\bgst", normalize_text(column)))), default=None)
    if best is not None and shares[best] >= _GSTIN_SHARE:
        roles.columns["gstin"], roles.gstin_by_values = best, True
    else:
        named = [column for column in columns if re.search(r"\bgst(?:in|r|n)?\b|\bgst no\b", normalize_text(column))]
        if named:
            roles.columns["gstin"] = named[0]

    for role, pattern in _ROLE_PATTERNS.items():
        for column in columns:
            name = normalize_text(column)
            if column in roles.columns.values() or _NOT_A_ROLE.search(name):
                continue
            if role == "invoice_value" and re.search(r"\btaxable\b", name):
                continue
            if pattern.search(name):
                roles.columns[role] = column
                break
    return roles


def _document_types_agree(df1: pd.DataFrame, df2: pd.DataFrame, column_1: str, column_2: str) -> bool:
    """Both document-type columns use the same words (invoice, credit note,
    debit note), so they can be part of the key without hiding real pairs."""
    for df, column in ((df1, column_1), (df2, column_2)):
        words = {normalize_text(value) for value in non_empty_values(df[column].tolist(), limit=500)}
        if not words or not words <= _DOCUMENT_TYPES:
            return False
    return True


def gst_preset(df1: pd.DataFrame, df2: pd.DataFrame) -> dict:
    """The GST preset for two sheets: whether to suggest it, and the rule it fills in.

    ``status`` is "certain" (one file was read from GSTR-2B JSON and the other
    holds GSTINs in its values), "likely" (both files hold GSTINs in their values and
    have the GST columns), "available" (the columns are there, but the values
    are not shown to be GSTINs: it is offered, not suggested) or "unavailable".
    """
    roles_1, roles_2 = _find_roles(df1), _find_roles(df2)
    missing_1, missing_2 = roles_1.missing(), roles_2.missing()
    result = {"id": GST_PRESET_ID, "label": GST_PRESET_LABEL, "status": "unavailable",
              "missing_1": missing_1, "missing_2": missing_2, "notes": [], "rule": None}
    if missing_1 or missing_2:
        return result

    # A file read from GSTR-2B JSON is GST data by itself (its import rows
    # carry "IMPORT" instead of a GSTIN); the other file must hold GSTINs.
    if (roles_1.is_gstr2b and roles_2.gstin_by_values) or (roles_2.is_gstr2b and roles_1.gstin_by_values):
        result["status"] = "certain"
    elif roles_1.gstin_by_values and roles_2.gstin_by_values:
        result["status"] = "likely"
    else:
        result["status"] = "available"

    c1, c2 = roles_1.columns, roles_2.columns
    keys_1, keys_2 = [c1["gstin"], c1["invoice_number"]], [c2["gstin"], c2["invoice_number"]]
    notes = result["notes"]
    if "document_type" in c1 and "document_type" in c2:
        if _document_types_agree(df1, df2, c1["document_type"], c2["document_type"]):
            keys_1.append(c1["document_type"])
            keys_2.append(c2["document_type"])
            notes.append("The document type is part of the key, so a credit note never pairs with an invoice of the same number.")
        else:
            notes.append("The document types use different words in the two files, so they are not part of the key.")
    else:
        notes.append("The files do not both have a document type, so a credit note can pair with an invoice of the same number.")

    mappings, tolerances = [], []
    if "supplier_name" in c1 and "supplier_name" in c2:
        mappings.append({"file_1_fields": [c1["supplier_name"]], "file_2_fields": [c2["supplier_name"]], "comparison_type": "company_name"})
        tolerances.append({"field": c1["supplier_name"], "similarity": GST_NAME_SIMILARITY})
    if "invoice_date" in c1 and "invoice_date" in c2:
        mappings.append({"file_1_fields": [c1["invoice_date"]], "file_2_fields": [c2["invoice_date"]], "comparison_type": "date"})
    for role in AMOUNT_ROLES:
        if role in c1 and role in c2:
            mappings.append({"file_1_fields": [c1[role]], "file_2_fields": [c2[role]], "comparison_type": "numeric"})
            tolerances.append({"field": c1[role], "amount": GST_AMOUNT_TOLERANCE})
    unpaired = [role for role in AMOUNT_ROLES if (role in c1) != (role in c2)]
    if unpaired:
        notes.append("Not compared, because only one file has them: " + ", ".join(role.replace("_", " ") for role in unpaired) + ".")

    result["rule"] = {
        "matching_strategy": {
            "primary_key_source": keys_1,
            "primary_key_destination": keys_2,
            # Repeated on purpose: near-identical invoice numbers are proposed
            # only when a "must also match" column exists, and this keeps them
            # within the same supplier (ADR-0001, action item 4).
            "secondary_conditions": [{
                "source_column": c1["gstin"], "destination_column": c2["gstin"], "comparison_method": "exact_text",
            }],
        },
        "reconciliation_mapping": mappings,
        "tolerances": tolerances,
        "date_format": "day_first",
    }
    return result
