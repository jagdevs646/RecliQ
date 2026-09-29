"""Bank statement and GST return formats.

Each parser turns a finance file into ordinary tables ("sheets") that the rest
of RecliQ treats like any spreadsheet, so pairing, keys, passes, pre-checks and
saved setups work unchanged:

* MT940 (SWIFT customer statement), CAMT.053 (ISO 20022 XML statement; the
  CAMT.052/054 report and notification variants share its structure) and BAI2
  become two sheets: ``Transactions`` (one row per booking, with a signed
  amount and separate debit/credit columns) and ``Balances`` (opening and
  closing balance per statement with a check that the bookings explain the
  movement).
* GSTR-2B (and the similar GSTR-2A) JSON from the GST portal becomes one
  ``GSTR-2B`` sheet whose columns are exactly the ones the GST reconciliation
  expects, so it can be compared with a purchase register directly.

Dates are written as ISO ``YYYY-MM-DD`` text so they are never ambiguous.
Parsers only read data; they never evaluate or fetch anything, and XML is
parsed with entity expansion and external references disabled.
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

MT940, CAMT053, BAI2, GSTR2B = "mt940", "camt053", "bai2", "gstr2b"

FORMAT_LABELS = {
    MT940: "MT940 bank statement",
    CAMT053: "CAMT.053 bank statement",
    BAI2: "BAI2 bank statement",
    GSTR2B: "GSTR-2B return",
}

# Extensions that always mean one format. ``.txt`` is sniffed: it may be a
# delimited table, an MT940 or a BAI2 file.
EXTENSION_FORMATS = {
    ".sta": MT940,
    ".mt940": MT940,
    ".940": MT940,
    ".bai": BAI2,
    ".bai2": BAI2,
    ".xml": CAMT053,
    ".json": GSTR2B,
}

TRANSACTIONS_SHEET = "Transactions"
BALANCES_SHEET = "Balances"
GSTR2B_SHEET = "GSTR-2B"

TRANSACTION_COLUMNS = [
    "Account", "Statement", "Booking Date", "Value Date", "Debit/Credit", "Amount", "Signed Amount",
    "Debit", "Credit", "Currency", "Transaction Code", "Transaction Type", "Reference", "Bank Reference",
    "Counterparty", "Narrative", "Reversal", "Status",
]
BALANCE_COLUMNS = [
    "Account", "Statement", "Currency", "Opening Date", "Opening Balance", "Credits", "Debits",
    "Transactions", "Calculated Closing", "Closing Date", "Closing Balance", "Balance Check",
]
GSTR2B_COLUMNS = [
    "GSTR", "NAME OF TRADER/FIRM/COMPANY", "INVOICE NO.", "INVOICE DATE", "TAXABLE VALUE", "IGST", "CGST",
    "SGST", "CESS", "INVOICE VALUE", "SECTION", "DOCUMENT TYPE", "ITC AVAILABLE", "ITC UNAVAILABLE REASON",
    "REVERSE CHARGE", "PLACE OF SUPPLY", "SUPPLIER FILING DATE", "SUPPLIER RETURN PERIOD", "RETURN PERIOD",
    "ORIGINAL INVOICE NO.", "ORIGINAL INVOICE DATE", "IRN", "SOURCE",
]


class FinanceFormatError(ValueError):
    """The file is not a readable statement/return of the expected format."""


# ── Detection ─────────────────────────────────────────────────────────────
def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


_MT940_TAGS = re.compile(r"^:(20|25|28C?|60[FM]|61|62[FM]):", re.MULTILINE)


def sniff_text(head: bytes) -> str | None:
    """Recognise MT940 or BAI2 from the start of a text file."""
    text = _decode(head).lstrip()
    if text.startswith("01,") and re.search(r"^\s*0[23],", text, re.MULTILINE):
        return BAI2
    tags = {match.group(1)[:2] for match in _MT940_TAGS.finditer(text)}
    if {"20", "25"} <= tags and ({"60", "61", "62"} & tags):
        return MT940
    return None


def format_for(extension: str, head: bytes) -> str | None:
    """The finance format of a file, or None for ordinary tables."""
    extension = extension.lower()
    if extension in EXTENSION_FORMATS:
        return EXTENSION_FORMATS[extension]
    if extension == ".txt":
        return sniff_text(head)
    return None


def sheet_names(fmt: str) -> list[str]:
    return [GSTR2B_SHEET] if fmt == GSTR2B else [TRANSACTIONS_SHEET, BALANCES_SHEET]


def parse_bytes(data: bytes, fmt: str) -> "OrderedDict[str, pd.DataFrame]":
    parser = {MT940: parse_mt940, CAMT053: parse_camt, BAI2: parse_bai2, GSTR2B: parse_gstr2b}.get(fmt)
    if parser is None:
        raise FinanceFormatError(f"Unknown finance format: {fmt}")
    return parser(data)


def parse_file(path: Path, fmt: str) -> "OrderedDict[str, pd.DataFrame]":
    return parse_bytes(Path(path).read_bytes(), fmt)


# ── Shared statement model ────────────────────────────────────────────────
@dataclass
class _Statement:
    account: str = ""
    number: str = ""
    currency: str = ""
    opening: Decimal | None = None
    opening_date: str = ""
    closing: Decimal | None = None
    closing_date: str = ""
    note: str = ""
    rows: list[dict] = field(default_factory=list)


def _money(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _transaction_row(
    statement: _Statement,
    *,
    booking_date: str,
    value_date: str,
    credit: bool | None,
    amount: Decimal,
    currency: str = "",
    code: str = "",
    kind: str = "",
    reference: str = "",
    bank_reference: str = "",
    counterparty: str = "",
    narrative: str = "",
    reversal: bool = False,
    status: str = "Booked",
) -> dict:
    """One booking. ``credit`` None means the direction is unknown (a custom
    BAI2 code); such rows get no signed amount rather than a guessed one."""
    signed = None if credit is None else (amount if credit else -amount)
    return {
        "Account": statement.account,
        "Statement": statement.number,
        "Booking Date": booking_date,
        "Value Date": value_date or booking_date,
        "Debit/Credit": "Unknown" if credit is None else ("Credit" if credit else "Debit"),
        "Amount": float(amount),
        "Signed Amount": _money(signed),
        "Debit": float(amount) if credit is False else None,
        "Credit": float(amount) if credit is True else None,
        "Currency": currency or statement.currency,
        "Transaction Code": code,
        "Transaction Type": kind,
        "Reference": reference.strip(),
        "Bank Reference": bank_reference.strip(),
        "Counterparty": counterparty.strip(),
        "Narrative": re.sub(r"\s+", " ", narrative).strip(),
        "Reversal": "Yes" if reversal else "No",
        "Status": status,
    }


def _statement_tables(statements: list[_Statement], label: str) -> "OrderedDict[str, pd.DataFrame]":
    if not statements:
        raise FinanceFormatError(f"No statements were found in this {label}.")
    rows = [row for statement in statements for row in statement.rows]
    balances = []
    for statement in statements:
        booked = [row for row in statement.rows if row["Status"] == "Booked"]
        credits = sum((Decimal(str(row["Credit"])) for row in booked if row["Credit"] is not None), Decimal(0))
        debits = sum((Decimal(str(row["Debit"])) for row in booked if row["Debit"] is not None), Decimal(0))
        unknown = sum(1 for row in booked if row["Debit/Credit"] == "Unknown")
        calculated = None if statement.opening is None else statement.opening + credits - debits
        if statement.note:
            check = statement.note
        elif statement.opening is None or statement.closing is None:
            check = "No opening/closing balance in the file"
        elif unknown:
            check = f"Not checked: {unknown} booking(s) have an unknown direction"
        else:
            gap = statement.closing - calculated
            check = "Balanced" if gap == 0 else f"Off by {gap:,.2f}"
        balances.append({
            "Account": statement.account,
            "Statement": statement.number,
            "Currency": statement.currency,
            "Opening Date": statement.opening_date,
            "Opening Balance": _money(statement.opening),
            "Credits": float(credits),
            "Debits": float(debits),
            "Transactions": len(booked),
            "Calculated Closing": _money(calculated),
            "Closing Date": statement.closing_date,
            "Closing Balance": _money(statement.closing),
            "Balance Check": check,
        })
    return OrderedDict([
        (TRANSACTIONS_SHEET, pd.DataFrame(rows, columns=TRANSACTION_COLUMNS)),
        (BALANCES_SHEET, pd.DataFrame(balances, columns=BALANCE_COLUMNS)),
    ])


def _yymmdd(text: str) -> str:
    try:
        return datetime.strptime(text, "%y%m%d").date().isoformat()
    except ValueError as exc:
        raise FinanceFormatError(f"Invalid date '{text}'.") from exc


# ── MT940 ─────────────────────────────────────────────────────────────────
_FIELD = re.compile(r"^:(\d{2}[A-Z]?):", re.MULTILINE)
_BALANCE = re.compile(r"^(?P<mark>[CD])(?P<date>\d{6})(?P<ccy>[A-Z]{3})(?P<amount>\d+(?:,\d*)?)$")
_STATEMENT_LINE = re.compile(
    r"^(?P<value>\d{6})(?P<entry>\d{4})?(?P<mark>RC|RD|EC|ED|C|D)(?P<funds>[A-Z])?"
    r"(?P<amount>\d+,\d*)(?P<type>[NFS][A-Z0-9]{3})(?P<rest>.*)$",
    re.DOTALL,
)
_MT940_TYPES = {
    "TRF": "Transfer", "CHK": "Cheque", "CHG": "Charges", "INT": "Interest", "DDT": "Direct debit",
    "STO": "Standing order", "COM": "Commission", "MSC": "Miscellaneous", "DIV": "Dividend",
    "FEX": "Foreign exchange", "LDP": "Loan deposit", "RTI": "Returned item", "BOE": "Bill of exchange",
    "CLR": "Cash letter", "COL": "Collection", "DCR": "Documentary credit", "ECK": "Eurocheque",
    "EQA": "Equivalent amount", "SEC": "Securities", "TCK": "Travellers cheque", "VDA": "Value date adjustment",
}


def _mt940_amount(text: str) -> Decimal:
    try:
        return Decimal(text.replace(",", ".").rstrip(".") or "0")
    except InvalidOperation as exc:
        raise FinanceFormatError(f"Invalid amount '{text}'.") from exc


def _mt940_balance(value: str) -> tuple[Decimal, str, str]:
    match = _BALANCE.match(value.strip().split("\n")[0].strip())
    if not match:
        raise FinanceFormatError(f"Invalid MT940 balance '{value.strip()}'.")
    amount = _mt940_amount(match["amount"])
    return (-amount if match["mark"] == "D" else amount), _yymmdd(match["date"]), match["ccy"]


def _entry_date(value_date: str, mmdd: str | None) -> str:
    """The 4-digit entry date carries no year: take the value date's year,
    adjusted when the two straddle a year end."""
    if not mmdd:
        return value_date
    value = date.fromisoformat(value_date)
    try:
        entry = date(value.year, int(mmdd[:2]), int(mmdd[2:]))
    except ValueError:
        return value_date
    if (entry - value).days > 180:
        entry = entry.replace(year=value.year - 1)
    elif (value - entry).days > 180:
        entry = entry.replace(year=value.year + 1)
    return entry.isoformat()


def _structured_86(text: str) -> tuple[str, str]:
    """German-style ``?20..?29`` sub-fields: counterparty is ``?32``+``?33``."""
    if not re.search(r"\?\d{2}", text):
        return text, ""
    parts = dict(re.findall(r"\?(\d{2})([^?]*)", text.replace("\n", "")))
    narrative = " ".join(parts[key] for key in sorted(parts) if "20" <= key <= "29" or "60" <= key <= "63")
    counterparty = " ".join(parts.get(key, "") for key in ("32", "33")).strip()
    return narrative or text, counterparty


def parse_mt940(data: bytes) -> "OrderedDict[str, pd.DataFrame]":
    text = _decode(data).replace("\r\n", "\n").replace("\r", "\n")
    # SWIFT envelopes ({1:...}{2:...}{4: ... -}) only wrap the fields.
    text = re.sub(r"\{[1235]:[^{}]*\}", "", text)
    text = text.replace("{4:", "\n").replace("-}", "\n")
    matches = list(_FIELD.finditer(text))
    if not matches:
        raise FinanceFormatError("This is not an MT940 statement: no :20:/:25:/:61: fields were found.")

    statements: list[_Statement] = []
    current: _Statement | None = None
    last_row: dict | None = None
    for index, match in enumerate(matches):
        tag = match.group(1)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[match.end():end].strip("\n")
        if tag == "20" or current is None:
            current = _Statement()
            statements.append(current)
            last_row = None
            if tag == "20":
                continue
        if tag == "25":
            current.account = value.strip()
        elif tag.startswith("28"):
            current.number = value.strip()
        elif tag in {"60F", "60M"}:
            current.opening, current.opening_date, current.currency = _mt940_balance(value)
        elif tag in {"62F", "62M"}:
            current.closing, current.closing_date, currency = _mt940_balance(value)
            current.currency = current.currency or currency
        elif tag == "61":
            line = _STATEMENT_LINE.match(value.strip())
            if not line:
                raise FinanceFormatError(f"Invalid MT940 statement line ':61:{value.strip()[:40]}'.")
            value_date = _yymmdd(line["value"])
            mark = line["mark"]
            credit = mark in {"C", "RD", "EC"}
            rest, _, supplementary = line["rest"].partition("\n")
            reference, _, bank_reference = rest.partition("//")
            code = line["type"]
            last_row = _transaction_row(
                current,
                booking_date=_entry_date(value_date, line["entry"]),
                value_date=value_date,
                credit=credit,
                amount=_mt940_amount(line["amount"]),
                code=code,
                kind=_MT940_TYPES.get(code[1:], "") + (" (reversal)" if mark.startswith("R") else ""),
                reference="" if reference.strip().upper() == "NONREF" else reference,
                bank_reference=bank_reference,
                narrative=supplementary,
                reversal=mark.startswith("R"),
            )
            current.rows.append(last_row)
        elif tag == "86" and last_row is not None:
            narrative, counterparty = _structured_86(value)
            last_row["Narrative"] = re.sub(r"\s+", " ", f"{last_row['Narrative']} {narrative}").strip()
            last_row["Counterparty"] = last_row["Counterparty"] or counterparty

    statements = [statement for statement in statements if statement.account or statement.rows]
    if not any(statement.account for statement in statements):
        raise FinanceFormatError("This is not an MT940 statement: the account field (:25:) is missing.")
    return _statement_tables(statements, "MT940 file")


# ── CAMT.053 (ISO 20022) ──────────────────────────────────────────────────
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element, *path: str):
    for name in path:
        if element is None:
            return None
        element = next((item for item in element if _local(item.tag) == name), None)
    return element


def _children(element, name: str) -> list:
    return [] if element is None else [item for item in element if _local(item.tag) == name]


def _text(element, *path: str) -> str:
    found = _child(element, *path) if path else element
    return (found.text or "").strip() if found is not None and found.text else ""


def _iso_date(element) -> str:
    """``<Dt>`` or ``<DtTm>`` inside a date container, as YYYY-MM-DD."""
    value = _text(element, "Dt") or _text(element, "DtTm")
    return value[:10]


def _decimal(text: str, what: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise FinanceFormatError(f"Invalid {what} '{text}'.") from exc


def _first_text(element, *paths: tuple[str, ...]) -> str:
    for path in paths:
        if value := _text(element, *path):
            return value
    return ""


def _party_name(details, role: str) -> str:
    parties = _child(details, "RltdPties")
    return _first_text(parties, (role, "Nm"), (role, "Pty", "Nm"))


_CAMT_STATUS = {"": "Booked", "BOOK": "Booked", "PDNG": "Pending", "INFO": "Information", "FUTR": "Future"}


def parse_camt(data: bytes) -> "OrderedDict[str, pd.DataFrame]":
    from defusedxml import ElementTree
    from defusedxml.common import DefusedXmlException

    try:
        root = ElementTree.fromstring(data)
    except DefusedXmlException as exc:
        raise FinanceFormatError("The XML uses entities or external references, which are not allowed.") from exc
    except Exception as exc:  # ParseError
        raise FinanceFormatError(f"The file is not valid XML: {exc}") from exc

    container = next(
        (child for child in root if _local(child.tag) in {"BkToCstmrStmt", "BkToCstmrAcctRpt", "BkToCstmrDbtCdtNtfctn"}),
        None,
    )
    if _local(root.tag) != "Document" or container is None:
        raise FinanceFormatError("This XML is not an ISO 20022 bank statement (CAMT.053/052/054).")

    statements: list[_Statement] = []
    for report in [item for item in container if _local(item.tag) in {"Stmt", "Rpt", "Ntfctn"}]:
        account = _child(report, "Acct")
        statement = _Statement(
            account=_first_text(account, ("Id", "IBAN"), ("Id", "Othr", "Id")),
            number=_text(report, "ElctrncSeqNb") or _text(report, "LglSeqNb") or _text(report, "Id"),
            currency=_text(account, "Ccy"),
        )
        for balance in _children(report, "Bal"):
            code = _first_text(balance, ("Tp", "CdOrPrtry", "Cd"), ("Tp", "CdOrPrtry", "Prtry"))
            amount_element = _child(balance, "Amt")
            amount = _decimal(_text(amount_element), "balance")
            if _text(balance, "CdtDbtInd") == "DBIT":
                amount = -amount
            statement.currency = statement.currency or (amount_element.get("Ccy", "") if amount_element is not None else "")
            when = _iso_date(_child(balance, "Dt"))
            if code in {"OPBD", "PRCD"} and statement.opening is None:
                statement.opening, statement.opening_date = amount, when
            elif code == "CLBD":
                statement.closing, statement.closing_date = amount, when

        for entry in _children(report, "Ntry"):
            amount_element = _child(entry, "Amt")
            entry_amount = _decimal(_text(amount_element), "amount")
            currency = amount_element.get("Ccy", "") if amount_element is not None else ""
            credit = _text(entry, "CdtDbtInd") == "CRDT"
            status = _text(entry, "Sts") or _text(entry, "Sts", "Cd")
            reversal = _text(entry, "RvslInd").lower() == "true"
            bank_code = _child(entry, "BkTxCd")
            code = "-".join(
                part for part in (
                    _text(bank_code, "Domn", "Cd"),
                    _text(bank_code, "Domn", "Fmly", "Cd"),
                    _text(bank_code, "Domn", "Fmly", "SubFmlyCd"),
                ) if part
            ) or _text(bank_code, "Prtry", "Cd")
            common = {
                "booking_date": _iso_date(_child(entry, "BookgDt")),
                "value_date": _iso_date(_child(entry, "ValDt")),
                "credit": credit,
                "currency": currency,
                "code": code,
                "reversal": reversal,
                "status": _CAMT_STATUS.get(status, status.title()),
            }
            details = [tx for block in _children(entry, "NtryDtls") for tx in _children(block, "TxDtls")]
            amounts = [_text(tx, "Amt") or _text(tx, "AmtDtls", "TxAmt", "Amt") for tx in details]
            # A batch booking lists each payment; split it only when every
            # part has its own amount, otherwise keep the booking whole.
            split = len(details) > 1 and all(amounts)
            for tx, part_amount in (zip(details, amounts) if split else [(details[0] if details else None, "")]):
                narrative = " ".join(
                    [_text(item) for item in _children(_child(tx, "RmtInf"), "Ustrd")]
                    + [_text(entry, "AddtlNtryInf"), _text(tx, "AddtlTxInf")]
                )
                counterparty = _party_name(tx, "Dbtr" if credit else "Cdtr")
                statement.rows.append(_transaction_row(
                    statement,
                    amount=_decimal(part_amount, "amount") if part_amount else entry_amount,
                    kind=_text(tx, "Purp", "Cd"),
                    reference=_first_text(
                        tx, ("RmtInf", "Strd", "CdtrRefInf", "Ref"), ("Refs", "EndToEndId"), ("Refs", "InstrId")
                    ).replace("NOTPROVIDED", ""),
                    bank_reference=_text(entry, "AcctSvcrRef") or _text(tx, "Refs", "AcctSvcrRef"),
                    counterparty=counterparty,
                    narrative=narrative,
                    **common,
                ))
        statements.append(statement)
    return _statement_tables(statements, "CAMT file")


# ── BAI2 ──────────────────────────────────────────────────────────────────
_BAI_TYPES = {
    "010": "Opening ledger", "015": "Closing ledger", "040": "Opening available", "045": "Closing available",
    "100": "Total credits", "400": "Total debits", "115": "Lockbox deposit", "142": "ACH credit received",
    "165": "Preauthorized ACH credit", "175": "Check deposit package", "195": "Incoming money transfer",
    "206": "Book transfer credit", "275": "ZBA credit", "301": "Commercial deposit", "399": "Miscellaneous credit",
    "451": "ACH debit received", "455": "Preauthorized ACH debit", "475": "Check paid",
    "495": "Outgoing money transfer", "506": "Book transfer debit", "575": "ZBA debit",
    "698": "Miscellaneous fees", "699": "Miscellaneous debit",
}
# Currencies without minor units; BAI2 amounts carry implied decimals.
_ZERO_DECIMAL = {"JPY", "KRW", "CLP", "ISK", "VND", "XAF", "XOF", "UGX", "PYG"}


def _bai_records(text: str) -> list[list[str]]:
    """Split into records and join ``88`` continuations onto the record before."""
    records: list[list[str]] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.endswith("/"):
            line = line[:-1]
        code, _, rest = line.partition(",")
        if code == "88" and records:
            if records[-1][0] == "16" and len(records[-1]) >= 7:
                records[-1][-1] = f"{records[-1][-1]} {rest}".strip()  # Continued free text.
            else:
                records[-1].extend(rest.split(","))
            continue
        if code == "16":
            # The last field is free text and may contain commas.
            records.append([code, *rest.split(",", 5)])
        else:
            records.append([code, *rest.split(",")])
    return records


def _bai_amount(text: str, currency: str) -> Decimal:
    text = text.strip()
    if not text:
        return Decimal(0)
    try:
        cents = Decimal(text)
    except InvalidOperation as exc:
        raise FinanceFormatError(f"Invalid BAI2 amount '{text}'.") from exc
    return cents if currency in _ZERO_DECIMAL else cents / 100


def _skip_funds(fields: list[str], position: int) -> int:
    """Return the position after a funds-type field and its extra values."""
    funds = fields[position].strip().upper() if position < len(fields) else ""
    position += 1
    if funds == "S":
        return position + 3
    if funds == "V":
        return position + 2
    if funds == "D":
        count = int(fields[position] or 0) if position < len(fields) and fields[position].strip().isdigit() else 0
        return position + 1 + 2 * count
    return position


def _bai_date(text: str) -> str:
    return _yymmdd(text.strip()) if text.strip() else ""


def parse_bai2(data: bytes) -> "OrderedDict[str, pd.DataFrame]":
    records = _bai_records(_decode(data))
    if not records or records[0][0] != "01":
        raise FinanceFormatError("This is not a BAI2 file: it must start with a 01 file header record.")

    statements: list[_Statement] = []
    group_currency, group_date = "", ""
    current: _Statement | None = None
    control: Decimal = Decimal(0)
    for record in records:
        code = record[0]
        if code == "02":
            group_date = _bai_date(record[4] if len(record) > 4 else "")
            group_currency = (record[6] if len(record) > 6 else "").strip()
        elif code == "03":
            currency = (record[2] if len(record) > 2 else "").strip() or group_currency
            current = _Statement(account=record[1].strip() if len(record) > 1 else "", number=group_date, currency=currency)
            statements.append(current)
            control = Decimal(0)
            position = 3
            while position + 1 < len(record) and record[position].strip():
                type_code, amount_text = record[position].strip(), record[position + 1]
                amount = _bai_amount(amount_text, currency)
                control += _decimal(amount_text.strip() or "0", "amount")
                if type_code == "010":
                    current.opening, current.opening_date = amount, group_date
                elif type_code == "015":
                    current.closing, current.closing_date = amount, group_date
                # type, amount, item count, then the funds type (and its extras).
                position = _skip_funds(record, position + 3)
        elif code == "16":
            if current is None:
                raise FinanceFormatError("BAI2 transaction record (16) found outside an account (03).")
            fields = record[1:]
            type_code = fields[0].strip() if fields else ""
            amount_text = fields[1] if len(fields) > 1 else ""
            control += _decimal(amount_text.strip() or "0", "amount")
            # Re-split after the funds type: bank ref, customer ref, free text.
            tail = ",".join(fields[2:])
            parts = tail.split(",")
            position = _skip_funds(parts, 0)
            remainder = parts[position:]
            bank_reference = remainder[0] if remainder else ""
            customer_reference = remainder[1] if len(remainder) > 1 else ""
            narrative = ",".join(remainder[2:]) if len(remainder) > 2 else ""
            number = int(type_code) if type_code.isdigit() else 0
            credit = True if 100 <= number <= 399 else False if 400 <= number <= 699 else None
            kind = _BAI_TYPES.get(type_code) or f"BAI code {type_code}" + ("" if credit is not None else " (direction not defined)")
            current.rows.append(_transaction_row(
                current,
                booking_date=group_date,
                value_date=group_date,
                credit=credit,
                amount=abs(_bai_amount(amount_text, current.currency)),
                code=type_code,
                kind=kind,
                reference=customer_reference,
                bank_reference=bank_reference,
                narrative=narrative,
            ))
        elif code == "49" and current is not None and len(record) > 1 and record[1].strip():
            declared = _decimal(record[1].strip(), "control total")
            if declared != control:
                # Reported in the Balances sheet, not rejected: a person decides.
                current.note = f"Control total {declared} does not equal the sum of amounts {control}; the file may be incomplete"
    return _statement_tables(statements, "BAI2 file")


# ── GSTR-2B / GSTR-2A JSON ────────────────────────────────────────────────
_SECTIONS = {
    "b2b": ("B2B", "inv"),
    "b2ba": ("B2B amendment", "inv"),
    "cdnr": ("Credit/debit note", "nt"),
    "cdnra": ("Credit/debit note amendment", "nt"),
    "isd": ("ISD", "doclist"),
    "isda": ("ISD amendment", "doclist"),
    "impg": ("Import of goods", None),
    "impgsez": ("Import of goods from SEZ", "boe"),
}
_TAX_KEYS = {
    "TAXABLE VALUE": ("txval",),
    "IGST": ("igst", "iamt"),
    "CGST": ("cgst", "camt"),
    "SGST": ("sgst", "samt"),
    "CESS": ("cess", "csamt"),
}


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _gst_date(value) -> str:
    text = str(value or "").strip()
    for pattern in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d-%m-%y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return text


def _amounts(document: dict) -> dict[str, float]:
    """Invoice-level amounts, or the sum of its rate-wise items."""
    items = document.get("items") or document.get("itms") or []
    sources = [item.get("itm_det", item) for item in items if isinstance(item, dict)] or [document]
    return {
        column: round(sum(_number(source.get(key)) for source in sources for key in keys if key in source), 2)
        for column, keys in _TAX_KEYS.items()
    }


def _yes_no(value) -> str:
    return {"Y": "Yes", "N": "No"}.get(str(value or "").upper(), str(value or ""))


def parse_gstr2b(data: bytes) -> "OrderedDict[str, pd.DataFrame]":
    try:
        payload = json.loads(_decode(data))
    except json.JSONDecodeError as exc:
        raise FinanceFormatError(f"The file is not valid JSON: {exc.msg} (line {exc.lineno}).") from exc
    body = payload.get("data", payload) if isinstance(payload, dict) else None
    documents = body.get("docdata", body) if isinstance(body, dict) else None
    if not isinstance(documents, dict) or not any(key in documents for key in _SECTIONS):
        raise FinanceFormatError(
            "This JSON is not a GSTR-2B (or GSTR-2A) return: no b2b, cdnr, isd or impg sections were found."
        )
    return_period = str(body.get("rtnprd", "") or body.get("fp", ""))

    rows: list[dict] = []
    for section, (label, list_key) in _SECTIONS.items():
        for supplier in documents.get(section) or []:
            if not isinstance(supplier, dict):
                continue
            entries = supplier.get(list_key) if list_key else [supplier]
            for document in entries or []:
                if not isinstance(document, dict):
                    continue
                note_type = str(document.get("typ") or document.get("ntty") or document.get("doctyp") or "").upper()
                is_note = section.startswith("cdn") or note_type in {"C", "D"}
                doc_type = "Credit note" if note_type == "C" else "Debit note" if note_type == "D" and is_note else "Invoice"
                if section.startswith("imp"):
                    doc_type = "Bill of entry"
                sign = -1 if doc_type == "Credit note" else 1
                amounts = {key: value * sign + 0.0 for key, value in _amounts(document).items()}  # No -0.0.
                total = _number(document.get("val")) * sign
                if not total:
                    total = round(sum(amounts.values()), 2)
                rows.append({
                    "GSTR": supplier.get("ctin", "") or ("IMPORT" if section == "impg" else ""),
                    "NAME OF TRADER/FIRM/COMPANY": supplier.get("trdnm", "")
                    or (f"Import (port {document.get('portcode', '')})" if section == "impg" else ""),
                    "INVOICE NO.": str(
                        document.get("inum") or document.get("ntnum") or document.get("nt_num")
                        or document.get("docnum") or document.get("boenum") or ""
                    ),
                    "INVOICE DATE": _gst_date(
                        document.get("dt") or document.get("idt") or document.get("nt_dt")
                        or document.get("docdt") or document.get("boedt")
                    ),
                    **amounts,
                    "INVOICE VALUE": round(total, 2),
                    "SECTION": label,
                    "DOCUMENT TYPE": doc_type,
                    "ITC AVAILABLE": _yes_no(document.get("itcavl") or document.get("itcelg")),
                    "ITC UNAVAILABLE REASON": str(document.get("rsn", "") or ""),
                    "REVERSE CHARGE": _yes_no(document.get("rev") or document.get("rchrg")),
                    "PLACE OF SUPPLY": str(document.get("pos", "") or ""),
                    "SUPPLIER FILING DATE": _gst_date(supplier.get("supfildt")),
                    "SUPPLIER RETURN PERIOD": str(supplier.get("supprd", "") or ""),
                    "RETURN PERIOD": return_period,
                    "ORIGINAL INVOICE NO.": str(
                        document.get("oinum") or document.get("ontnum") or document.get("oinvnum") or ""
                    ),
                    "ORIGINAL INVOICE DATE": _gst_date(
                        document.get("oidt") or document.get("ontdt") or document.get("oinvdt")
                    ),
                    "IRN": str(document.get("irn", "") or ""),
                    "SOURCE": str(document.get("srctyp", "") or ""),
                })
    if not rows:
        raise FinanceFormatError("The GSTR-2B file contains no documents.")
    return OrderedDict([(GSTR2B_SHEET, pd.DataFrame(rows, columns=GSTR2B_COLUMNS))])
