"""PDF tables that run over several pages are read as one table."""
import hashlib
from pathlib import Path

import pytest

from types import SimpleNamespace

import pandas as pd

from app.reconciliation_engine.ingestion import extract_file_metadata, read_table_data
from app.reconciliation_engine.ingestion.pdf_tables import _extract_table, find_table, merge_fragments

HEADER = ["Invoice No", "Date", "Amount"]


def _rows(start: int, count: int) -> list[list[str]]:
    return [[f"INV-{n:03d}", f"{(n % 28) + 1:02d}/08/2026", f"{n * 100:,}.00"] for n in range(start, start + count)]


# ── Merging rules ──────────────────────────────────────────────────────────
def test_a_header_repeated_on_every_page_gives_one_table():
    tables = merge_fragments([(1, 1, [HEADER, *_rows(1, 3)]), (2, 1, [HEADER, *_rows(4, 3)]), (3, 1, [HEADER, *_rows(7, 2)])])
    assert len(tables) == 1
    table = tables[0]
    assert table.header == HEADER and table.pages == [1, 2, 3]
    assert [row[0] for row in table.rows] == [f"INV-{n:03d}" for n in range(1, 9)]


def test_a_page_without_a_header_continues_the_table_and_keeps_its_first_row():
    tables = merge_fragments([(1, 1, [HEADER, *_rows(1, 3)]), (2, 1, _rows(4, 3))])
    assert len(tables) == 1
    assert tables[0].rows[3][0] == "INV-004"  # Not swallowed as column names.
    assert len(tables[0].rows) == 6


def test_a_changed_header_starts_a_different_table():
    other = ["Vendor", "GSTIN", "Tax"]
    tables = merge_fragments([
        (1, 1, [HEADER, *_rows(1, 2)]),
        (2, 1, [other, ["Acme", "27ABCDE1234F1Z5", "180.00"]]),
    ])
    assert [table.header for table in tables] == [HEADER, other]
    assert tables[1].pages == [2]


def test_a_different_number_of_columns_starts_a_different_table():
    tables = merge_fragments([(1, 1, [HEADER, *_rows(1, 2)]), (2, 1, [["Summary", "Total"], ["Invoices", "300.00"]])])
    assert len(tables) == 2


def test_headers_match_ignoring_case_line_breaks_and_punctuation():
    tables = merge_fragments([
        (1, 1, [["Invoice\nNo.", "Date", "Amount"], *_rows(1, 2)]),
        (2, 1, [["INVOICE NO", "date", "Amount "], *_rows(3, 2)]),
    ])
    assert len(tables) == 1 and len(tables[0].rows) == 4
    assert tables[0].header == ["Invoice No.", "Date", "Amount"]


def test_a_dash_in_an_amount_column_is_not_mistaken_for_a_new_header():
    tables = merge_fragments([(1, 1, [HEADER, *_rows(1, 3)]), (2, 1, [["INV-004", "05/08/2026", "-"], *_rows(5, 1)])])
    assert len(tables) == 1 and len(tables[0].rows) == 5


def test_one_odd_cell_in_a_continuation_row_does_not_split_the_table():
    header = ["Date", "Reference", "Debit", "Credit", "Balance"]
    rows = [["01/08/2026", f"UTR{n}", "100.00", "0", "500.00"] for n in range(5)]
    odd = ["02/08/2026", "UTR9", "100.00", "text that spilled here", "400.00"]
    tables = merge_fragments([(1, 1, [header, *rows]), (2, 1, [odd, *rows])])
    assert len(tables) == 1 and len(tables[0].rows) == 11


def test_a_text_only_table_continues_when_the_data_cannot_tell_otherwise():
    names = [["Vendor", "City"], ["Acme", "Mumbai"], ["Globex", "Pune"]]
    tables = merge_fragments([(1, 1, names), (2, 1, [["Initech", "Delhi"]])])
    assert len(tables) == 1 and tables[0].rows[-1] == ["Initech", "Delhi"]


def test_header_rows_repeated_inside_a_page_and_blank_rows_are_dropped():
    tables = merge_fragments([(1, 1, [HEADER, *_rows(1, 2), ["", None, ""], HEADER, *_rows(3, 1)])])
    assert len(tables[0].rows) == 3


def test_two_tables_on_one_page_stay_separate_and_the_second_can_continue_on_the_next():
    ledger = ["Account", "Debit", "Credit"]
    tables = merge_fragments([
        (1, 1, [HEADER, *_rows(1, 2)]),
        (1, 2, [ledger, ["Cash", "100.00", "0.00"]]),
        (2, 1, [["Bank", "0.00", "100.00"]]),
    ])
    assert [len(table.rows) for table in tables] == [2, 2]
    assert tables[1].fragments == [(1, 2), (2, 1)]


def test_blank_and_repeated_column_names_are_made_unique():
    df = merge_fragments([(1, 1, [["Amount", "", "Amount"], ["1.00", "x", "2.00"]])])[0].to_dataframe()
    assert list(df.columns) == ["Amount", "Column 2", "Amount (2)"]


def test_ids_saved_before_merging_still_find_their_table():
    tables = merge_fragments([(1, 1, [HEADER, *_rows(1, 2)]), (2, 1, _rows(3, 2)), (3, 1, [["A", "B"], ["1", "2"]])])
    assert find_table(tables, "page_2_table_1") is tables[0]
    assert find_table(tables, "table_2") is tables[1]
    with pytest.raises(ValueError, match="does not exist"):
        find_table(tables, "table_9")


# ── Text that overflows its cell ──────────────────────────────────────────
def _chars(text: str, x: float, top: float = 10.0, size: float = 6.0, width: float = 3.0) -> list[dict]:
    """Characters drawn left to right from ``x``, as pdfplumber reports them."""
    return [
        {"text": ch, "x0": x + i * width, "x1": x + (i + 1) * width, "top": top, "doctop": top,
         "bottom": top + size, "size": size, "upright": True}
        for i, ch in enumerate(text)
    ]


def _table(edges: list[float], top: float = 8.0, bottom: float = 18.0):
    cells = [(edges[i], top, edges[i + 1], bottom) for i in range(len(edges) - 1)]
    row = SimpleNamespace(bbox=(edges[0], top, edges[-1], bottom), cells=cells)
    return SimpleNamespace(bbox=row.bbox, rows=[row], cells=cells)


def test_text_overflowing_into_the_next_cells_stays_in_its_own_cell():
    # Narration (0-40) runs on over Debit (40-70), drawn on top of its "0",
    # and Credit (70-100) holds a right-aligned amount.
    table = _table([0, 40, 70, 100])
    chars = _chars("ACME INDUSTRIES Receipt", 1) + _chars("0", 66) + _chars("342919.16", 71.5)
    assert _extract_table(table, chars) == [["ACME INDUSTRIES Receipt", "0", "342919.16"]]


def test_neighbouring_cells_written_close_together_stay_apart():
    # "113440.79" ends 1pt before its edge and "DR" starts 1pt after it.
    table = _table([0, 40, 60])
    chars = _chars("113440.79", 12) + _chars("DR", 41)
    assert _extract_table(table, chars) == [["113440.79", "DR"]]


def test_a_right_aligned_number_touching_overflowing_text_is_split_off():
    # "CITY LEASE Rent" (0-40 column) overflows into Debit (40-68) and ends
    # exactly where the right-aligned "3565.68" starts: no gap to split on.
    table = _table([0, 40, 68])
    chars = _chars("CITY LEASE Rent" + "3565.68", 1)  # Number spans 46-67.
    assert _extract_table(table, chars) == [["CITY LEASE Rent", "3565.68"]]


def test_parsed_copies_from_an_older_reader_are_not_reused(tmp_path: Path):
    path = tmp_path / "statement.csv"
    path.write_text("Reference,Amount\nUTR1,100\n", encoding="utf-8")
    # A pickle left beside the upload by the previous reader (garbled cells).
    stale = pd.DataFrame({"Reference": ["UTR1"], "Amount": ["m3e4r2 R9e1c9e.i2p"]})
    digest = hashlib.sha1(b"statement.csv").hexdigest()[:12]
    stale.to_pickle(path.with_name(f"{path.name}.sheet-{digest}.pkl"))
    df = read_table_data(path, path.name, "statement.csv")
    assert df["Amount"].astype(str).tolist() == ["100"]


# ── A real PDF ─────────────────────────────────────────────────────────────
def _pdf(path: Path, pages: list[list[list[list[str]]]]) -> None:
    """Write a minimal PDF whose tables are ruled grids (what pdfplumber detects)."""
    def text(value: str) -> str:
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    streams = []
    for tables in pages:
        ops, top = [], 780
        for rows in tables:
            columns = len(rows[0])
            left, width, height = 40, 150, 18
            bottom = top - height * len(rows)
            for index in range(len(rows) + 1):
                y = top - index * height
                ops.append(f"{left} {y} m {left + width * columns} {y} l S")
            for index in range(columns + 1):
                x = left + index * width
                ops.append(f"{x} {top} m {x} {bottom} l S")
            for r, row in enumerate(rows):
                for c, cell in enumerate(row):
                    ops.append(f"BT /F1 9 Tf {left + c * width + 4} {top - r * height - 13} Td ({text(cell)}) Tj ET")
            top = bottom - 40
        streams.append("\n".join(ops).encode("latin-1"))

    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", None, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for stream in streams:
        page_number, content_number = len(objects) + 1, len(objects) + 2
        kids.append(f"{page_number} 0 R")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_number} 0 R >>".encode())
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()

    output, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(output)
    output += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    output += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    output += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(bytes(output))


def test_a_pdf_table_over_three_pages_is_one_sheet(tmp_path: Path):
    path = tmp_path / "statement.pdf"
    _pdf(path, [
        [[HEADER, *_rows(1, 4)]],                           # page 1: header + data
        [_rows(5, 4)],                                      # page 2: data only
        [[HEADER, *_rows(9, 2)], [["Vendor", "Total"], ["Acme", "1,900.00"]]],  # page 3: header repeated, then another table
    ])
    sheets = extract_file_metadata(path, path.name)
    assert [sheet["id"] for sheet in sheets] == ["table_1", "table_2"]
    assert sheets[0]["name"] == "Table 1 (pages 1–3, 10 rows)"

    df = read_table_data(path, path.name, "table_1")
    assert list(df.columns) == HEADER
    assert df["Invoice No"].tolist() == [f"INV-{n:03d}" for n in range(1, 11)]
    assert read_table_data(path, path.name, "table_2").to_dict("records") == [{"Vendor": "Acme", "Total": "1,900.00"}]


def test_a_pdf_narration_longer_than_its_column_does_not_garble_the_amounts(tmp_path: Path):
    path = tmp_path / "books.pdf"
    narration = "ZENITH SERVICES Professional Fees and Retainer"  # ~190pt in a 150pt column.
    _pdf(path, [[[["Reference", "Narration", "Debit", "Credit"],
                  ["UTR1", narration, "145559.26", "0"],
                  ["UTR2", "Rent", "0", "98136.02"]]]])
    df = read_table_data(path, path.name, "table_1")
    assert df.to_dict("records") == [
        {"Reference": "UTR1", "Narration": narration, "Debit": "145559.26", "Credit": "0"},
        {"Reference": "UTR2", "Narration": "Rent", "Debit": "0", "Credit": "98136.02"},
    ]
