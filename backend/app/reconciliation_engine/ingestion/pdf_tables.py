"""Tables in a PDF, merged across pages.

A PDF paginates one logical table: page 2 either repeats the header row or
simply continues with data rows. Reading every page's table as its own sheet
forced people to pair "page 1" with "page 1" and so on, and took the first
data row of a headerless continuation page as its column names.

Page fragments are read in order (top to bottom, page by page) and each one
either continues the table before it or starts a new table:

* same header as the open table (compared ignoring case, spacing and
  punctuation)          -> continuation; the repeated header row is dropped;
* no header, same number of columns -> continuation; every row is data;
* different header, or a different number of columns -> a new table.

A fragment "has no header" unless its first row puts text where the open
table holds amounts or dates (e.g. "Amount" in a column of numbers). When
the data gives no way to tell, the fragment is treated as a continuation:
a missed continuation is the failure people see, while two different tables
with the same shape back to back is rare.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from app.reconciliation_engine.cache import parse_date_value, to_number

try:
    import pdfplumber
    from pdfplumber import utils
except ImportError:  # pragma: no cover - listed in requirements.txt
    pdfplumber = None

# Placeholders that sit in amount columns without being text labels.
_NEUTRAL = {"", "-", "--", "—", "–", "nil", "na", "n/a", "none", "null", "nan"}
# A column counts as amounts/dates when this share of its values parse as such.
_VALUE_SHARE = 0.8
# Rows sampled (from the end of the open table) to learn its column kinds.
_KIND_SAMPLE = 200


@dataclass
class PdfTable:
    number: int
    header: list[str]
    rows: list[list[str]] = field(default_factory=list)
    # (page, table on that page), both 1-based, for every fragment merged in.
    fragments: list[tuple[int, int]] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"table_{self.number}"

    @property
    def pages(self) -> list[int]:
        return sorted({page for page, _ in self.fragments})

    @property
    def name(self) -> str:
        first, last = self.pages[0], self.pages[-1]
        pages = f"page {first}" if first == last else f"pages {first}–{last}"
        return f"Table {self.number} ({pages}, {len(self.rows):,} rows)"

    def column_is_value(self, index: int) -> bool | None:
        """True when a column holds amounts/dates, False for text, None if empty."""
        cells = [row[index] for row in self.rows[-_KIND_SAMPLE:] if row[index].strip().lower() not in _NEUTRAL]
        if not cells:
            return None
        return sum(_is_value(cell) for cell in cells) >= _VALUE_SHARE * len(cells)

    def to_dataframe(self) -> pd.DataFrame:
        width = len(self.header)
        rows = [(row + [""] * width)[:width] for row in self.rows]
        return pd.DataFrame(rows, columns=_column_names(self.header))


def clean_cell(value: object) -> str:
    """Cell text with line breaks and repeated spaces collapsed."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _header_key(row: list[str]) -> tuple[str, ...]:
    return tuple(re.sub(r"[^0-9a-z]+", " ", cell.lower()).strip() for cell in row)


def _is_value(cell: str) -> bool:
    return to_number(cell) is not None or parse_date_value(cell) is not None


def _looks_like_header(row: list[str], table: PdfTable) -> bool:
    """Text where the open table holds amounts or dates marks a new header row.

    A header labels every column ("Debit", "Credit", "Balance"), so it needs
    text in at least half of the open table's amount/date columns; one odd
    cell in a data row is not enough to split the table."""
    value_columns = [index for index in range(len(row)) if table.column_is_value(index)]
    labelled = sum(
        1 for index in value_columns
        if row[index].strip().lower() not in _NEUTRAL and not _is_value(row[index])
    )
    return bool(value_columns) and labelled * 2 >= len(value_columns)


def _column_names(header: list[str]) -> list[str]:
    """Unique, non-blank column names (blank -> "Column 3", repeats -> "Amount (2)")."""
    names: list[str] = []
    seen: dict[str, int] = {}
    for index, cell in enumerate(header):
        name = cell or f"Column {index + 1}"
        count = seen.get(name.lower(), 0) + 1
        seen[name.lower()] = count
        names.append(name if count == 1 else f"{name} ({count})")
    return names


def merge_fragments(fragments: list[tuple[int, int, list[list[object]]]]) -> list[PdfTable]:
    """Merge page fragments ``(page, table_on_page, rows)``, given in reading order."""
    tables: list[PdfTable] = []
    open_table: PdfTable | None = None
    for page, index, raw_rows in fragments:
        rows = [[clean_cell(cell) for cell in row] for row in raw_rows]
        rows = [row for row in rows if any(row)]
        if not rows:
            continue
        first = rows[0]
        if open_table is not None and len(first) == len(open_table.header):
            if _header_key(first) == _header_key(open_table.header):
                body = rows[1:]  # The header repeated at the top of the page.
            elif not _looks_like_header(first, open_table):
                body = rows  # A headerless continuation page.
            else:
                body = None
            if body is not None:
                open_table.fragments.append((page, index))
                header_key = _header_key(open_table.header)
                open_table.rows.extend(row for row in body if _header_key(row) != header_key)
                continue
        open_table = PdfTable(number=len(tables) + 1, header=first, fragments=[(page, index)])
        header_key = _header_key(first)
        open_table.rows.extend(row for row in rows[1:] if _header_key(row) != header_key)
        tables.append(open_table)
    return tables


def _text_runs(chars: list[dict], column_edges: list[float]) -> list[list[dict]]:
    """Split characters, in drawing order, into runs of text drawn in one go.

    Text longer than its cell is still drawn: "ACME INDUSTRIES Customer
    Receipt" in a narrow Narration column runs on over the Debit and Credit
    cells, on top of their numbers. Assigning single characters to the cell
    they sit over interleaves both texts ("m3e4r2 R9e1c9e.i2p"); a run keeps
    the overflowing words together so they can go back to their own cell.

    A run ends at a jump back, a line change or a word-sized gap, and also at
    any visible gap across a column edge: the texts of two neighbouring
    cells ("113440.79" | "DR") can be closer together than two words are.
    When the PDF writes spaces as characters, words touch, so any visible gap
    starts another cell's text ("CITY LEASE Rent" | "108684").
    """
    has_spaces = any(char["text"] == " " for char in chars)
    runs: list[list[dict]] = []
    for char in chars:
        if runs:
            prev = runs[-1][-1]
            size = max(prev["size"], char["size"], 1.0)
            gap = char["x0"] - prev["x1"]
            same_line = abs(char["top"] - prev["top"]) <= size * 0.3
            # Cell text may end a hair past its own edge ("UTR26000260" by 0.1pt).
            slack = size * 0.3
            across_edge = gap > size * 0.1 and any(
                prev["x1"] - slack <= edge <= char["x0"] + slack for edge in column_edges
            )
            word_gap = size * (0.15 if has_spaces else 0.6)
            if same_line and -size * 0.3 <= gap <= word_gap and not across_edge:
                runs[-1].append(char)
                continue
        runs.append([char])
    return runs


def _run_cell(run: list[dict], cells: list[tuple[int, tuple[float, float, float, float]]]) -> int | None:
    """Index of the cell a run belongs to: the one it lies in, or when it
    spills over, the one it is aligned to (left-aligned text overflows to the
    right, right-aligned numbers overflow to the left)."""
    start, end = run[0]["x0"], run[-1]["x1"]
    start_mid = (run[0]["x0"] + run[0]["x1"]) / 2
    end_mid = (run[-1]["x0"] + run[-1]["x1"]) / 2
    first = next((i for i, (x0, _, x1, _) in cells if x0 <= start_mid < x1), None)
    last = next((i for i, (x0, _, x1, _) in cells if x0 <= end_mid < x1), None)
    if first is None or last is None or first == last:
        return first if first is not None else last
    lookup = dict(cells)
    start_padding = start - lookup[first][0]
    end_padding = lookup[last][2] - end
    # Text that starts at its cell's left padding is left-aligned, whatever
    # edge its overflow happens to stop near.
    size = run[0]["size"]
    return last if start_padding > size and end_padding < start_padding else first


_NUMBER_TAIL = re.compile(r"[-(]?[\d,]*\.?\d+\)?$")


def _split_touching_number(run: list[dict], cells: list[tuple[int, tuple[float, float, float, float]]]) -> list[list[dict]]:
    """Separate a right-aligned number from overflowing text it touches.

    "CITY LEASE Rent" overflowing into the Debit cell can end exactly where
    the right-aligned "3565.68" begins, leaving no gap to split on. When a run
    crosses into another cell and ends in a number that lies wholly in that
    cell, flush with its right edge and straight after a letter, the number
    is that cell's own value.
    """
    start_mid = (run[0]["x0"] + run[0]["x1"]) / 2
    end_cell = next((cell for _, cell in cells if cell[0] <= run[-1]["x0"] and run[-1]["x1"] <= cell[2] + 1), None)
    if end_cell is None or end_cell[0] <= start_mid or end_cell[2] - run[-1]["x1"] > run[-1]["size"]:
        return [run]
    text = "".join(char["text"] for char in run)
    tail = _NUMBER_TAIL.search(text)
    split = tail.start() if tail else 0
    if split and text[split - 1].isalpha() and run[split]["x0"] >= end_cell[0]:
        return [run[:split], run[split:]]
    return [run]


def _extract_table(table, page_chars: list[dict]) -> list[list[str | None]]:
    """Like pdfplumber's ``Table.extract``, but keeps text that spills into a
    neighbouring cell in the cell it was written in."""
    x0, top, x1, bottom = table.bbox
    chars = [
        char for char in page_chars
        if x0 <= (char["x0"] + char["x1"]) / 2 < x1 and top <= (char["top"] + char["bottom"]) / 2 < bottom
    ]
    output: list[list[str | None]] = []
    column_edges = sorted({edge for cell in table.cells for edge in (cell[0], cell[2])})
    runs = _text_runs(chars, column_edges)
    for row in table.rows:
        _, row_top, _, row_bottom = row.bbox
        cells = [(i, cell) for i, cell in enumerate(row.cells) if cell is not None]
        cell_chars: dict[int, list[dict]] = {i: [] for i, _ in cells}
        for whole in runs:
            middle = (whole[0]["top"] + whole[0]["bottom"]) / 2
            if not row_top <= middle < row_bottom:
                continue
            for run in _split_touching_number(whole, cells):
                if (index := _run_cell(run, cells)) is not None:
                    cell_chars[index].extend(run)
        output.append([
            None if cell is None else (utils.extract_text(cell_chars[i]) if cell_chars[i] else "")
            for i, cell in enumerate(row.cells)
        ])
    return output


def _read_fragments(file_path: Path) -> list[tuple[int, int, list[list[object]]]]:
    if pdfplumber is None:  # pragma: no cover
        raise ImportError("pdfplumber is required to read tables from PDFs.")
    fragments = []
    with pdfplumber.open(file_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            # Reading order: top to bottom, then left to right.
            found = sorted(page.find_tables(), key=lambda table: (round(table.bbox[1]), table.bbox[0]))
            page_chars = page.chars
            for index, table in enumerate(found, start=1):
                fragments.append((page_number, index, _extract_table(table, page_chars)))
            page.flush_cache()  # Keep memory flat on long documents.
    return fragments


_cache: "OrderedDict[tuple, list[PdfTable]]" = OrderedDict()


def extract_tables(file_path: Path) -> list[PdfTable]:
    """Every logical table in a PDF (memoized per file version)."""
    path = Path(file_path)
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    try:
        tables = merge_fragments(_read_fragments(path))
    except ImportError:
        raise
    except Exception as exc:
        raise ValueError(f"Failed to read tables from the PDF: {exc}") from exc
    if not tables:
        raise ValueError("No tables were found in this PDF. Only PDFs with ruled (bordered) tables can be read.")
    _cache[key] = tables
    while len(_cache) > 8:
        _cache.popitem(last=False)
    return tables


_LEGACY_ID = re.compile(r"^page_(\d+)_table_(\d+)$")


def find_table(tables: list[PdfTable], sheet_id: str) -> PdfTable:
    """A table by id: "table_2", or a per-page id saved before tables were merged
    ("page_3_table_1" -> the merged table that page's fragment belongs to)."""
    for table in tables:
        if table.id == sheet_id:
            return table
    if legacy := _LEGACY_ID.match(sheet_id):
        fragment = (int(legacy.group(1)), int(legacy.group(2)))
        for table in tables:
            if fragment in table.fragments:
                return table
    available = ", ".join(table.name for table in tables)
    raise ValueError(f"Table '{sheet_id}' does not exist in this PDF. Available: {available}.")
