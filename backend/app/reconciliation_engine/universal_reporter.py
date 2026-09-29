"""Excel reconciliation report.

The workbook is written for someone who has never seen RecliQ: the summary
states what happened in plain words, every number is a stored value (so
removing a tab never breaks a figure), and each detail tab answers one
question. Internal matching columns (normalized keys, IDs, scores used only by
the engine) are never shown.

Tabs (each only when included):
    01 Summary          status, key figures, results table with links, setup
    02 Differences      one row per field that differs on a matched record
    03 Only in <file 1> records with no partner in file 2, with the reason
    04 Only in <file 2> records with no partner in file 1
    05 Match Review     secondary-key and ambiguous matches to confirm
    06 Matched          records that agree on every compared field
    07 Checks           do the counts balance, and how matching was done
    08 Sheet Rules      per-rule configuration (multi-rule jobs)
    09 Auto-resolved    exceptions explained by the organization's rules
"""
from __future__ import annotations

import math
import re
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable

import openpyxl
import pandas as pd
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.series import DataPoint
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.hyperlink import Hyperlink
from openpyxl.worksheet.table import Table, TableStyleInfo


def excel_writer_engine() -> str:
    return "openpyxl"


class ReportConfig:
    def __init__(
        self,
        include_summary: bool = True,
        include_exceptions: bool = True,
        include_matched: bool = True,
        include_missing_file_1: bool = True,
        include_missing_file_2: bool = True,
        # Historical name: now controls the "Match Review" tab.
        include_field_differences: bool = True,
        include_controls: bool = True,
        include_auto_resolved: bool = True,
        date_format: str = "YYYY-MM-DD",
        number_format: str = "#,##0.00",
    ):
        self.include_summary = include_summary
        self.include_exceptions = include_exceptions
        self.include_matched = include_matched
        self.include_missing_file_1 = include_missing_file_1
        self.include_missing_file_2 = include_missing_file_2
        self.include_field_differences = include_field_differences
        self.include_controls = include_controls
        self.include_auto_resolved = include_auto_resolved
        self.date_format = date_format
        self.number_format = number_format


# Engine columns that mean nothing to a reader of the report.
_HIDDEN_COLUMNS = {
    "File Pair ID", "Sheet Rule ID", "Sheet Pair", "MATCH TYPE", "MATCH CONFIDENCE", "MATCH STATUS",
    "MATCH THRESHOLD", "IDENTITY CLASSIFICATION", "MATCH EXPLANATION", "GROUPED ROWS",
    "COMPOSITE MATCH KEY", "MATCHED COMPOSITE KEY", "MATCH KEY", "MATCHED KEY",
    "SECONDARY KEY", "MATCHED SECONDARY KEY", "GROUP CLASSIFICATION", "CANDIDATE KEY",
    "MATCH PASS", "NORMALIZATION APPLIED", "MATCH METHOD", "REVIEW HISTORY", "LEARNED CONFIDENCE", "AUTO-RESOLVED BY",
}
_HIDDEN_PREFIXES = ("NORM_", "MATCHED NORM_", "__")

_RESULT_LABELS = {
    "EXACT_MATCH": "Matched",
    "EXCEPTION_MATCH": "Matched by secondary keys",
    "AMBIGUOUS_MATCH": "Several possible matches",
    "NOT_FOUND": "Not found",
}

_INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def _clean_table_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "", name)
    if cleaned and cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned[:30] or "TableData"


def _clean_file_label(name: str) -> str:
    """'January.xlsx (Sales)' -> 'January (Sales)'."""
    return re.sub(r"\.(xlsx|xls|csv)\b", "", str(name).strip(), flags=re.IGNORECASE)


def _cell_value(value: Any) -> Any:
    """Excel-safe value: blanks for NaN, plain dates for midnight timestamps."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        value = value.replace(tzinfo=None)
        return value.date() if value.time() == time(0) else value
    if isinstance(value, (list, dict, set, tuple)):
        return str(value)
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _row_number(record: dict, file_number: int) -> Any:
    for key in (f"ROW (FILE {file_number})", f"ROW (File {file_number})"):
        if key in record:
            return record[key]
    return next((value for key, value in record.items() if str(key).upper().startswith("ROW")), None)


def _visible(column: str) -> bool:
    return column not in _HIDDEN_COLUMNS and not str(column).startswith(_HIDDEN_PREFIXES) and not str(column).upper().startswith("ROW")


class UniversalReporter:
    def __init__(self, data: dict, config: ReportConfig, output_path: Path):
        self.data = data
        self.config = config
        self.output_path = output_path
        self.font_family = "Arial"
        self.colors = {
            "primary": "1F3864",       # Deep navy
            "accent": "2E5395",        # Slate blue
            "header_fg": "FFFFFF",
            "pass": "1E7B34",          # Green
            "warning": "B36A00",       # Amber
            "exception": "C0392B",     # Red
            "critical": "A6192E",
            "review": "6C4AA0",        # Violet
            "neutral_bg": "F2F2F2",
            "text_dark": "000000",
            "text_muted": "595959",
            "border": "D9D9D9",
            "pass_bg": "E3F4E8",
            "warn_bg": "FFF3D6",
            "fail_bg": "FADBD8",
            "link": "1F5FBF",
        }
        meta = data.get("metadata", {})
        self.file1 = meta.get("file_1_name", "File 1") or "File 1"
        self.file2 = meta.get("file_2_name", "File 2") or "File 2"
        self.multi_rule = len(data.get("sheet_rules") or []) > 1
        self.sheet_names = self._plan_sheet_names()
        self._table_names: set[str] = set()

    # ── Helpers ────────────────────────────────────────────────────────────
    def _thin_border(self, color: str | None = None) -> Border:
        side = Side(style="thin", color=color or self.colors["border"])
        return Border(left=side, right=side, top=side, bottom=side)

    def _font(self, size: float = 10, bold: bool = False, color: str | None = None, underline: str | None = None) -> Font:
        return Font(name=self.font_family, size=size, bold=bold, color=color or self.colors["text_dark"], underline=underline)

    def _plan_sheet_names(self) -> dict[str, str]:
        def only_in(prefix: str, name: str, fallback: str) -> str:
            label = _INVALID_SHEET_CHARS.sub("", _clean_file_label(name)).strip() or fallback
            return f"{prefix} Only in {label}"[:31].rstrip()

        only_1 = only_in("03", self.file1, "File 1")
        only_2 = only_in("04", self.file2, "File 2")
        if only_1[3:] == only_2[3:]:  # Same label on both sides (e.g. two sheets of one workbook).
            only_1, only_2 = "03 Only in File 1", "04 Only in File 2"
        return {
            "summary": "01 Summary",
            "differences": "02 Differences",
            "only_1": only_1,
            "only_2": only_2,
            "review": "05 Match Review",
            "matched": "06 Matched",
            "checks": "07 Checks",
            "rules": "08 Sheet Rules",
            "resolved": "09 Auto-resolved",
        }

    def _counts(self) -> dict[str, int]:
        stats = self.data.get("statistics", {})
        exceptions = self.data.get("exceptions", [])
        identity = stats.get("identity") or {}
        review_rows = self._review_records()
        differ = stats.get("mismatched")
        if differ is None:
            differ = len({row.get("Primary Key", row.get("Match Key")) for row in exceptions})
        return {
            "total_1": int(stats.get("total_file_1", 0) or 0),
            "total_2": int(stats.get("total_file_2", 0) or 0),
            "matched": len(self.data.get("matched_records", [])),
            "differ": int(differ or 0),
            "only_1": len(self.data.get("missing_in_file_2", [])),
            "only_2": len(self.data.get("missing_in_file_1", [])),
            "review": (identity.get("EXCEPTION_MATCH", 0) + identity.get("AMBIGUOUS_MATCH", 0)) if identity else len(review_rows),
            "resolved": len(self.data.get("auto_resolved") or []),
            "resolved_1": self._resolved_count("only_in_source"),
            "resolved_2": self._resolved_count("only_in_destination"),
        }

    def _resolved_count(self, kind: str) -> int:
        return sum(1 for row in self.data.get("auto_resolved") or [] if row.get("__KIND__") == kind)

    def _review_records(self) -> list[dict]:
        return [
            record
            for record in self.data.get("identity_resolution", [])
            if record.get("IDENTITY CLASSIFICATION") in {"EXCEPTION_MATCH", "AMBIGUOUS_MATCH"}
        ]

    def _sheet_label(self, record: dict) -> str:
        return str(record.get("Sheet Pair", "")).strip("[]")

    def _title(self, ws, title: str, subtitle: str, width: int) -> None:
        last = get_column_letter(max(width, 1))
        ws.merge_cells(f"A1:{last}1")
        ws["A1"] = title
        ws["A1"].font = self._font(14, True, self.colors["primary"])
        ws.row_dimensions[1].height = 22
        ws.merge_cells(f"A2:{last}2")
        ws["A2"] = subtitle
        ws["A2"].font = self._font(9.5, color=self.colors["text_muted"])
        ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[2].height = 30

    def _write_table(
        self,
        ws,
        headers: list[str],
        rows: Iterable[list[Any]],
        table_name: str,
        empty_message: str,
        number_columns: Iterable[int] = (),
        percent_columns: Iterable[int] = (),
    ) -> int:
        """Write a filterable table at row 4; return the number of data rows.

        Rows are appended in bulk and styled by the Excel table, which keeps
        large reports fast (no per-cell style objects).
        """
        headers = self._unique_headers(headers)
        ws.append([])  # Row 3 spacer (rows 1-2 hold the title).
        ws.append(headers)
        count = 0
        for row in rows:
            ws.append([_cell_value(value) for value in row])
            count += 1
        ws.freeze_panes = "A5"
        if count == 0:
            ws.cell(row=5, column=1, value=empty_message).font = self._font(10, color=self.colors["text_muted"])
            for col_idx in range(1, len(headers) + 1):
                cell = ws.cell(row=4, column=col_idx)
                cell.font = self._font(10, True, self.colors["header_fg"])
                cell.fill = PatternFill("solid", fgColor=self.colors["primary"])
        else:
            last = get_column_letter(len(headers))
            name = _clean_table_name(table_name)
            while name in self._table_names:
                name = f"{name[:27]}{len(self._table_names)}"
            self._table_names.add(name)
            table = Table(displayName=name, ref=f"A4:{last}{count + 4}")
            table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
            ws.add_table(table)
            for col_idx in number_columns:
                for (cell,) in ws.iter_rows(min_row=5, max_row=count + 4, min_col=col_idx, max_col=col_idx):
                    if _is_number(cell.value):
                        cell.number_format = self.config.number_format
            for col_idx in percent_columns:
                for (cell,) in ws.iter_rows(min_row=5, max_row=count + 4, min_col=col_idx, max_col=col_idx):
                    if _is_number(cell.value):
                        cell.number_format = "0.0%"
        self._fit_columns(ws, headers, count)
        return count

    @staticmethod
    def _unique_headers(headers: list[str]) -> list[str]:
        seen: dict[str, int] = {}
        unique = []
        for header in headers:
            text = str(header) if header not in (None, "") else "Column"
            key = text.lower()
            if key in seen:
                seen[key] += 1
                text = f"{text} ({seen[key]})"
            else:
                seen[key] = 1
            unique.append(text)
        return unique

    @staticmethod
    def _display_length(value: Any) -> int:
        if _is_number(value):
            return len(f"{value:,.2f}")  # As displayed, not the raw float repr.
        return len(str(value))

    def _fit_columns(self, ws, headers: list[str], count: int) -> None:
        sample_rows = list(ws.iter_rows(min_row=5, max_row=min(count, 200) + 4, values_only=True)) if count else []
        for col_idx, header in enumerate(headers, start=1):
            longest = max(
                [len(str(header)) + 2]  # Room for the filter button.
                + [self._display_length(row[col_idx - 1]) for row in sample_rows if col_idx - 1 < len(row) and row[col_idx - 1] is not None]
            )
            limit = 90 if header in {"Why it was not matched", "Explanation"} else 50
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max(longest + 2, 11), limit)

    # ── Workbook ───────────────────────────────────────────────────────────
    def generate(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        wb = openpyxl.Workbook()
        default_sheet = wb.active

        included = {
            "differences": self.config.include_exceptions,
            "only_1": self.config.include_missing_file_1,
            "only_2": self.config.include_missing_file_2,
            "review": self.config.include_field_differences and bool(self._review_records()),
            "matched": self.config.include_matched,
            "checks": self.config.include_controls,
            "rules": self._has_rule_breakdown(),
            "resolved": self.config.include_auto_resolved and bool(self.data.get("auto_resolved")),
        }
        if self.config.include_summary:
            self._generate_summary(wb, included)
        if included["differences"]:
            self._generate_differences(wb)
        if included["only_1"]:
            self._generate_only_in(wb, 1)
        if included["only_2"]:
            self._generate_only_in(wb, 2)
        if included["review"]:
            self._generate_review(wb)
        if included["matched"]:
            self._generate_matched(wb)
        if included["checks"]:
            self._generate_checks(wb)
        if included["rules"]:
            self._generate_sheet_rules(wb)
        if included["resolved"]:
            self._generate_auto_resolved(wb)

        if default_sheet in wb.worksheets:
            wb.remove(default_sheet)
        if not wb.worksheets:
            wb.create_sheet("Empty").cell(row=1, column=1, value="No sections selected")
        if self.sheet_names["summary"] in wb.sheetnames:
            wb.active = wb[self.sheet_names["summary"]]
        wb.save(self.output_path)

    # ── 01 Summary ─────────────────────────────────────────────────────────
    def _generate_summary(self, wb: openpyxl.Workbook, included: dict[str, bool]) -> None:
        ws = wb.create_sheet(self.sheet_names["summary"])
        ws.sheet_properties.tabColor = self.colors["primary"]
        ws.views.sheetView[0].showGridLines = False
        counts = self._counts()
        border = self._thin_border()
        card_fill = PatternFill("solid", fgColor=self.colors["neutral_bg"])
        navy_fill = PatternFill("solid", fgColor=self.colors["primary"])

        # Content lives in columns B:H; the chart sits alone from column J.
        ws.column_dimensions["A"].width = 2
        ws.column_dimensions["B"].width = 32
        for column in "CDEFG":
            ws.column_dimensions[column].width = 13
        ws.column_dimensions["H"].width = 22
        ws.column_dimensions["I"].width = 3

        ws.merge_cells("B1:H1")
        ws["B1"] = "Reconciliation Report"
        ws["B1"].font = self._font(18, True, self.colors["header_fg"])
        ws["B1"].fill = navy_fill
        ws["B1"].alignment = Alignment(vertical="center", indent=1)
        ws.row_dimensions[1].height = 34
        ws.merge_cells("B2:H2")
        ws["B2"] = f"{self.file1}  compared with  {self.file2}   ·   Generated {datetime.now(timezone.utc).strftime('%d %b %Y')}"
        ws["B2"].font = self._font(10, color=self.colors["header_fg"])
        ws["B2"].fill = PatternFill("solid", fgColor=self.colors["accent"])
        ws["B2"].alignment = Alignment(vertical="center", indent=1)
        ws.row_dimensions[2].height = 20

        attention = counts["differ"] + counts["only_1"] + counts["only_2"]
        ws.merge_cells("B4:H4")
        if attention == 0 and counts["review"] == 0:
            status, fill, color = f"✔  All {counts['total_1']:,} records match. No action needed.", self.colors["pass_bg"], self.colors["pass"]
        else:
            status = f"⚠  {attention:,} item{'s' if attention != 1 else ''} need your attention — see the Results table below."
            fill, color = self.colors["warn_bg"], self.colors["warning"]
        ws["B4"] = status
        ws["B4"].font = self._font(12, True, color)
        ws["B4"].fill = PatternFill("solid", fgColor=fill)
        ws["B4"].alignment = Alignment(vertical="center", indent=1)
        ws.row_dimensions[4].height = 26

        compared = counts["matched"] + counts["differ"]
        rate = counts["matched"] / counts["total_1"] if counts["total_1"] else 0
        cards = [
            ("B6:B6", "B7:B7", f"Records in {self.file1}", counts["total_1"], "#,##0", self.colors["primary"]),
            ("C6:D6", "C7:D7", f"Records in {self.file2}", counts["total_2"], "#,##0", self.colors["primary"]),
            ("E6:F6", "E7:F7", "Matched, no differences", counts["matched"], "#,##0", self.colors["pass"]),
            ("G6:H6", "G7:H7", "Match rate", rate, "0.0%", self.colors["pass"] if rate >= 0.99 else self.colors["warning"]),
        ]
        for label_range, value_range, label, value, number_format, color in cards:
            ws.merge_cells(label_range)
            ws.merge_cells(value_range)
            label_cell = ws[label_range.split(":")[0]]
            label_cell.value = label
            label_cell.font = self._font(9, True, self.colors["text_muted"])
            value_cell = ws[value_range.split(":")[0]]
            value_cell.value = value
            value_cell.number_format = number_format
            value_cell.font = self._font(20, True, color)
            for cell_range in (label_range, value_range):
                for row in ws[cell_range]:
                    for cell in row:
                        cell.fill = card_fill
                        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[6].height = 28
        ws.row_dimensions[7].height = 34

        # Results: one line per outcome, each linked to the tab that lists it.
        row = 9
        row = self._section(ws, row, "RESULTS")
        headers = [("B", "Result"), ("C", "Records"), ("D", "What it means"), ("H", "Where to look")]
        ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=7)
        for column, text in headers:
            cell = ws[f"{column}{row}"]
            cell.value = text
            cell.font = self._font(10, True, self.colors["header_fg"])
            cell.fill = navy_fill
            cell.alignment = Alignment(horizontal="left" if column in "BD" else "center", vertical="center")
        results = [
            ("Matched – all fields agree", counts["matched"], "Found in both files and every compared field is the same.", "matched", self.colors["pass"]),
            ("Matched – values differ", counts["differ"], "Found in both files, but at least one compared field is different.", "differences", self.colors["exception"]),
            (f"Only in {self.file1}", counts["only_1"], f"In {self.file1} but no matching record was found in {self.file2}.", "only_1", self.colors["critical"]),
            (f"Only in {self.file2}", counts["only_2"], f"In {self.file2} but no matching record was found in {self.file1}.", "only_2", self.colors["critical"]),
        ]
        if self.data.get("identity_resolution"):
            results.append(("Needs review", counts["review"], "Matched through secondary keys with a slightly different primary key, or several possible matches were found.", "review", self.colors["review"]))
        if counts["resolved"]:
            results.append(("Auto-resolved by rules", counts["resolved"], "Recurring exceptions explained by your organization's rules, with the rule, resolution and GL account.", "resolved", self.colors["accent"]))
        first_result_row = row + 1
        for label, count, meaning, sheet_key, color in results:
            row += 1
            label_cell = ws.cell(row=row, column=2, value=label)
            label_cell.font = self._font(10, True, color)
            label_cell.alignment = Alignment(vertical="center", wrap_text=True)
            count_cell = ws.cell(row=row, column=3, value=count)
            count_cell.number_format = "#,##0"
            count_cell.font = self._font(11, True)
            count_cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=7)
            meaning_cell = ws.cell(row=row, column=4, value=meaning)
            meaning_cell.font = self._font(9, color=self.colors["text_muted"])
            meaning_cell.alignment = Alignment(wrap_text=True, vertical="center")
            link_cell = ws.cell(row=row, column=8)
            if included.get(sheet_key):
                sheet = self.sheet_names[sheet_key]
                link_cell.value = f"Open '{sheet}'"
                link_cell.hyperlink = Hyperlink(ref=link_cell.coordinate, location=f"'{sheet}'!A1")  # In-workbook link.
                link_cell.font = self._font(9, color=self.colors["link"], underline="single")
            else:
                link_cell.value = "—"
                link_cell.font = self._font(9, color=self.colors["text_muted"])
            link_cell.alignment = Alignment(horizontal="center", vertical="center")
            for col_idx in range(2, 9):
                ws.cell(row=row, column=col_idx).border = border
            ws.row_dimensions[row].height = 30
        last_result_row = row

        # How matching was done, in words.
        row = self._section(ws, row + 2, "HOW RECORDS WERE MATCHED")
        for label, value in self._matching_description(counts):
            label_cell = ws.cell(row=row, column=2, value=label)
            label_cell.font = self._font(10, True)
            label_cell.alignment = Alignment(vertical="center")
            ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=8)
            value_cell = ws.cell(row=row, column=3, value=value)
            value_cell.font = self._font(10)
            value_cell.alignment = Alignment(wrap_text=True, vertical="center")
            ws.row_dimensions[row].height = 30 if len(str(value)) > 80 else 18
            row += 1

        # Which fields differ most.
        row = self._section(ws, row + 1, "FIELDS WITH DIFFERENCES")
        field_counts = self._field_difference_counts()
        if not field_counts:
            ws.cell(row=row, column=2, value="No field differences were found.").font = self._font(10, color=self.colors["pass"])
            row += 1
        else:
            for column, text in (("B", "Field"), ("C", "Records"), ("D", "Share of matched records")):
                cell = ws[f"{column}{row}"]
                cell.value = text
                cell.font = self._font(10, True, self.colors["header_fg"])
                cell.fill = navy_fill
            ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=5)
            for field, count in field_counts[:15]:
                row += 1
                ws.cell(row=row, column=2, value=field).font = self._font(10)
                ws.cell(row=row, column=3, value=count).alignment = Alignment(horizontal="center")
                ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=5)
                share = ws.cell(row=row, column=4, value=count / compared if compared else 0)
                share.number_format = "0.0%"
                share.alignment = Alignment(horizontal="center")
                for col_idx in range(2, 6):
                    ws.cell(row=row, column=col_idx).border = border
            row += 1

        # A short reading guide.
        row = self._section(ws, row + 1, "HOW TO READ THIS REPORT")
        guide = [
            f"{self.sheet_names['differences']}: one row per field that differs on a matched record, with both values side by side.",
            f"{self.sheet_names['only_1']} / {self.sheet_names['only_2']}: records without a partner in the other file, and why.",
            f"{self.sheet_names['review']}: matches made through secondary keys, or records with several possible matches. Confirm them.",
            f"{self.sheet_names['matched']}: records that agree on every compared field.",
            f"{self.sheet_names['checks']}: confirms every record is accounted for.",
        ] + ([f"{self.sheet_names['resolved']}: exceptions your auto-resolution rules explained, and which rule did it."] if counts["resolved"] else [])
        for line in guide:
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=8)
            cell = ws.cell(row=row, column=2, value=f"•  {line}")
            cell.font = self._font(9.5, color=self.colors["text_muted"])
            row += 1

        # One chart, in its own columns (J onwards) so it never covers a cell.
        chart = BarChart()
        chart.type = "bar"
        chart.title = "Records by result"
        chart.legend = None
        chart.y_axis.majorGridlines = None
        chart.y_axis.delete = True
        chart.x_axis.scaling.orientation = "maxMin"  # Same order as the table.
        chart.width, chart.height = 15, 8
        chart.add_data(Reference(ws, min_col=3, min_row=first_result_row, max_row=last_result_row), titles_from_data=False)
        chart.set_categories(Reference(ws, min_col=2, min_row=first_result_row, max_row=last_result_row))
        chart.x_axis.delete = False  # Category names on the axis, counts on the bars.
        series = chart.series[0]
        series.dLbls = DataLabelList()
        series.dLbls.showVal = True
        series.dLbls.showSerName = False
        series.dLbls.showCatName = False
        series.dLbls.showLegendKey = False
        for index, (_, _, _, _, color) in enumerate(results):
            point = DataPoint(idx=index)
            point.graphicalProperties.solidFill = color
            point.graphicalProperties.line.solidFill = color
            series.data_points.append(point)
        ws.add_chart(chart, "J6")

    def _section(self, ws, row: int, title: str) -> int:
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=8)
        cell = ws.cell(row=row, column=2, value=title)
        cell.font = self._font(11, True, self.colors["primary"])
        cell.border = Border(bottom=Side(style="medium", color=self.colors["primary"]))
        ws.row_dimensions[row].height = 22
        return row + 1

    def _matching_description(self, counts: dict[str, int]) -> list[tuple[str, str]]:
        meta = self.data.get("metadata", {})
        rules = self.data.get("sheet_rules") or []
        if len(rules) > 1:
            description = [("Primary key", f"Each sheet rule uses its own keys — see '{self.sheet_names['rules']}'.")]
        else:
            rule = rules[0] if rules else {}
            source = rule.get("primary_key_source") or meta.get("matching_keys") or []
            destination = rule.get("primary_key_destination") or []
            key = " + ".join(source) + (f"  ↔  {' + '.join(destination)}" if destination else "")
            description = [("Primary key", f"{key or 'Key'} (spacing, case and punctuation differences are ignored)")]
            secondary = meta.get("secondary_keys") or [
                f"{condition.get('source_column')} ↔ {condition.get('destination_column')}"
                for condition in rule.get("secondary_conditions") or []
            ]
            description.append(
                ("Secondary keys", f"{'; '.join(secondary)} — must also match for records to be paired" if secondary else "None")
            )
        if meta.get("compared_fields"):
            description.append(("Compared fields", ", ".join(meta["compared_fields"])))
        if meta.get("transformations"):
            description.append(("Prepared before matching", "; ".join(meta["transformations"]) + " (original values are shown next to changed ones)"))
        passes = meta.get("matching_passes") or []
        if len(passes) > 1 or (passes and passes[0] != "Primary key"):
            counts_by_pass = meta.get("pass_counts") or {}
            description.append((
                "Matching passes (in order)",
                " → ".join(f"{label} ({counts_by_pass.get(label, 0):,} matched)" for label in passes)
                + ". Each pass only looks at records still unmatched; ties are never decided automatically.",
            ))
        rules_used = meta.get("normalization_rules_used") or {}
        if rules_used:
            top = sorted(rules_used.items(), key=lambda item: -item[1])[:6]
            description.append((
                "Name normalization used",
                ", ".join(f"{rule} ({count:,})" for rule, count in top) + " — spelling variants treated as the same name.",
            ))
        used = meta.get("resolution_rules_used") or {}
        if used:
            description.append((
                "Auto-resolution rules",
                ", ".join(f"{rule} ({count:,})" for rule, count in sorted(used.items(), key=lambda item: -item[1]))
                + " — exceptions they explain are listed separately, never deleted.",
            ))
        if meta.get("date_convention") and meta.get("date_convention", "").startswith("Month"):
            description.append(("Date format", meta["date_convention"]))
        combined = sum(
            1
            for category in ("matched_records", "missing_in_file_1", "missing_in_file_2")
            for record in self.data.get(category, [])
            if record.get("GROUPED ROWS")
        ) + len({row.get("Primary Key") for row in self.data.get("exceptions", []) if row.get("Grouped Rows")})
        if combined:
            description.append(
                ("Combined rows", f"{combined:,} record(s) were built by adding up rows that share the same key (see the 'Combined rows' column).")
            )
        return description

    def _field_difference_counts(self) -> list[tuple[str, int]]:
        records_by_field: dict[str, set] = {}
        for row in self.data.get("exceptions", []):
            identity = (row.get("Sheet Pair"), row.get("Primary Key", row.get("Match Key")))
            records_by_field.setdefault(str(row.get("Field", "")), set()).add(identity)
        return sorted(((field, len(keys)) for field, keys in records_by_field.items()), key=lambda item: -item[1])

    # ── 02 Differences ─────────────────────────────────────────────────────
    def _generate_differences(self, wb: openpyxl.Workbook) -> None:
        ws = wb.create_sheet(self.sheet_names["differences"])
        ws.sheet_properties.tabColor = self.colors["exception"]
        ws.views.sheetView[0].showGridLines = False
        records = self.data.get("exceptions", [])
        has_matched_key = any("Matched Key" in row for row in records)
        has_secondary = any(row.get("Secondary Key") for row in records)
        has_groups = any(row.get("Grouped Rows") for row in records)
        has_similarity = any(row.get("Similarity") for row in records)

        headers = (["Sheet"] if self.multi_rule else []) + [f"Key in {self.file1}"]
        headers += [f"Key in {self.file2}"] if has_matched_key else []
        headers += ["Secondary key"] if has_secondary else []
        headers += ["Field", f"{self.file1} value", f"{self.file2} value", "Difference", "Difference %"]
        headers += ["Similarity"] if has_similarity else []
        headers += ["Combined rows"] if has_groups else []
        headers += ["Reviewer notes"]

        def rows():
            for row in records:
                values = [self._sheet_label(row)] if self.multi_rule else []
                values.append(row.get("Primary Key", row.get("Match Key")))
                if has_matched_key:
                    values.append(row.get("Matched Key"))
                if has_secondary:
                    values.append(row.get("Secondary Key"))
                difference_pct = row.get("Difference %")
                values += [row.get("Field"), row.get("File 1 Value"), row.get("File 2 Value"), row.get("Difference"),
                           difference_pct if _is_number(difference_pct) else None]
                if has_similarity:
                    values.append(row.get("Similarity"))
                if has_groups:
                    values.append(row.get("Grouped Rows"))
                values.append(None)
                yield values

        self._title(
            ws,
            "Differences — matched records whose values disagree",
            f"Each row is one field that differs. A record with several differing fields appears once per field. "
            f"Difference = {self.file1} value − {self.file2} value.",
            len(headers),
        )
        value_start = headers.index(f"{self.file1} value") + 1
        self._write_table(
            ws, headers, rows(), "DifferencesTbl", "No differences — every matched record agrees on all compared fields.",
            number_columns=(value_start, value_start + 1, value_start + 2),
            percent_columns=(value_start + 3,),
        )

    # ── 03 / 04 Only in … ─────────────────────────────────────────────────
    def _generate_only_in(self, wb: openpyxl.Workbook, file_number: int) -> None:
        this_file, other_file = (self.file1, self.file2) if file_number == 1 else (self.file2, self.file1)
        ws = wb.create_sheet(self.sheet_names[f"only_{file_number}"])
        ws.sheet_properties.tabColor = self.colors["critical"]
        ws.views.sheetView[0].showGridLines = False
        # missing_in_file_2 holds file-1 records absent from file 2, and vice versa.
        records = self.data.get("missing_in_file_2" if file_number == 1 else "missing_in_file_1", [])

        data_columns = [column for column in dict.fromkeys(key for record in records for key in record) if _visible(column)]
        has_result = any(record.get("IDENTITY CLASSIFICATION") for record in records)
        has_reason = any(record.get("MATCH EXPLANATION") for record in records)
        has_groups = any(record.get("GROUPED ROWS") for record in records)
        headers = (["Sheet"] if self.multi_rule else []) + data_columns
        headers += ["Result"] if has_result else []
        headers += ["Why it was not matched"] if has_reason else []
        headers += ["Combined rows"] if has_groups else []
        headers += [f"Row in {this_file}"]

        def rows():
            for record in records:
                values = [self._sheet_label(record)] if self.multi_rule else []
                values += [record.get(column) for column in data_columns]
                if has_result:
                    values.append(_RESULT_LABELS.get(record.get("IDENTITY CLASSIFICATION"), record.get("IDENTITY CLASSIFICATION")))
                if has_reason:
                    values.append(record.get("MATCH EXPLANATION"))
                if has_groups:
                    values.append(record.get("GROUPED ROWS"))
                values.append(_row_number(record, file_number))
                yield values

        self._title(
            ws,
            f"Only in {this_file}",
            f"These records are in {this_file}, but no record in {other_file} has the same key"
            f"{' and secondary keys' if self.data.get('metadata', {}).get('secondary_keys') else ''}.",
            len(headers),
        )
        self._write_table(ws, headers, rows(), f"OnlyInFile{file_number}Tbl", f"Every record in {this_file} was matched.")

    # ── 05 Match Review ────────────────────────────────────────────────────
    def _generate_review(self, wb: openpyxl.Workbook) -> None:
        ws = wb.create_sheet(self.sheet_names["review"])
        ws.sheet_properties.tabColor = self.colors["review"]
        ws.views.sheetView[0].showGridLines = False
        records = self._review_records()
        headers = (["Sheet"] if self.multi_rule else []) + [
            "Result", f"Key in {self.file1}", f"Closest key in {self.file2}", "Explanation", "Key similarity", f"Row in {self.file1}",
        ]

        def rows():
            for record in records:
                exception = record.get("IDENTITY CLASSIFICATION") == "EXCEPTION_MATCH"
                values = [self._sheet_label(record)] if self.multi_rule else []
                keyless = record.get("MATCH PASS") and record.get("MATCH PASS") != "Primary key"
                values += [
                    f"Matched by {record['MATCH PASS'].split(': ', 1)[-1].lower()}" if keyless and exception
                    else _RESULT_LABELS.get(record.get("IDENTITY CLASSIFICATION")),
                    record.get("MATCH KEY"),
                    record.get("CANDIDATE KEY") or None,
                    record.get("MATCH EXPLANATION"),
                    record.get("MATCH CONFIDENCE") if exception else None,
                    _row_number(record, 1),
                ]
                yield values

        self._title(
            ws,
            "Match Review — please confirm these",
            "'Matched by secondary keys': the primary key differed slightly, but every secondary key matched one record. "
            "'Several possible matches': more than one record qualified, so none was chosen automatically.",
            len(headers),
        )
        self._write_table(ws, headers, rows(), "MatchReviewTbl", "Nothing to review.")

    # ── 06 Matched ─────────────────────────────────────────────────────────
    def _record_keys(self, row: dict) -> tuple[Any, Any]:
        meta = self.data.get("metadata", {})
        key_label = ", ".join(meta.get("matching_keys") or ["Key"])
        key_1 = row.get("COMPOSITE MATCH KEY", row.get("MATCH KEY"))
        if key_1 is None:
            key_1 = row.get(key_label, row.get(f"{key_label} (File1)", row.get(f"{key_label} (FILE 1)")))
        key_2 = row.get("MATCHED COMPOSITE KEY", row.get("MATCHED KEY"))
        if key_2 is None:
            key_2 = row.get(f"MATCHED {key_label}", row.get(f"{key_label} (File2)", row.get(f"{key_label} (FILE 2)", key_1)))
        return key_1, key_2

    def _generate_matched(self, wb: openpyxl.Workbook) -> None:
        ws = wb.create_sheet(self.sheet_names["matched"])
        ws.sheet_properties.tabColor = self.colors["pass"]
        ws.views.sheetView[0].showGridLines = False
        records = self.data.get("matched_records", [])
        has_secondary = any(record.get("SECONDARY KEY") for record in records)
        has_groups = any(record.get("GROUPED ROWS") for record in records)
        has_normalization = any(record.get("NORMALIZATION APPLIED") for record in records)
        headers = (["Sheet"] if self.multi_rule else []) + [f"Key in {self.file1}", f"Key in {self.file2}"]
        headers += ["Secondary key"] if has_secondary else []
        headers += ["How matched"]
        headers += ["Normalization applied"] if has_normalization else []
        headers += ["Combined rows"] if has_groups else []
        headers += [f"Row in {self.file1}", f"Row in {self.file2}"]

        def rows():
            for record in records:
                key_1, key_2 = self._record_keys(record)
                values = [self._sheet_label(record)] if self.multi_rule else []
                values += [key_1, key_2]
                if has_secondary:
                    values.append(record.get("SECONDARY KEY"))
                how = record.get("GROUP CLASSIFICATION") or "One-to-One Match"
                match_pass = record.get("MATCH PASS")
                if match_pass and match_pass != "Primary key":
                    how = f"{how} ({match_pass.split(': ', 1)[-1].lower()})"
                elif record.get("IDENTITY CLASSIFICATION") == "EXCEPTION_MATCH":
                    how = f"{how} (by secondary keys)"
                values.append(how)
                if has_normalization:
                    values.append(record.get("NORMALIZATION APPLIED"))
                if has_groups:
                    values.append(record.get("GROUPED ROWS"))
                values += [_row_number(record, 1), _row_number(record, 2)]
                yield values

        self._title(
            ws,
            "Matched — records that agree on every compared field",
            "No action needed. Listed so every record can be traced back to its row in each file.",
            len(headers),
        )
        self._write_table(ws, headers, rows(), "MatchedRecordsTbl", "No record matched on every compared field.")

    # ── 09 Auto-resolved ───────────────────────────────────────────────────
    def _generate_auto_resolved(self, wb: openpyxl.Workbook) -> None:
        ws = wb.create_sheet(self.sheet_names["resolved"])
        ws.sheet_properties.tabColor = self.colors["accent"]
        ws.views.sheetView[0].showGridLines = False
        records = self.data.get("auto_resolved") or []
        fixed = ["Exception", "Resolution", "Rule", "Rule Version", "Reason Code", "GL Account", "Record"]
        optional = ["Field", "File 1 Value", "File 2 Value", "Difference", "Matched With", "How Matched", "Match Confidence"]
        present = [column for column in optional if any(record.get(column) not in (None, "") for record in records)]
        skip = set(fixed) | set(optional) | {"Note"}
        data_columns = [
            column for column in dict.fromkeys(key for record in records for key in record)
            if column not in skip and _visible(column)
        ]
        headers = (["Sheet"] if self.multi_rule else []) + fixed + present + data_columns + ["Note", "Row"]

        def rows():
            for record in records:
                values = [self._sheet_label(record)] if self.multi_rule else []
                values += [record.get(column) for column in fixed + present + data_columns]
                kind = record.get("__KIND__")
                values += [record.get("Note"), _row_number(record, 2 if kind == "only_in_destination" else 1)]
                yield values

        self._title(
            ws,
            "Auto-resolved — explained by your rules",
            "These exceptions matched an auto-resolution rule. They are not deleted: each shows the rule and version "
            "that explained it, the resolution, reason code and GL account. Rules never decide between several possible matches.",
            len(headers),
        )
        self._write_table(ws, headers, rows(), "AutoResolvedTbl", "No exception was auto-resolved.")

    # ── 07 Checks ──────────────────────────────────────────────────────────
    def _generate_checks(self, wb: openpyxl.Workbook) -> None:
        ws = wb.create_sheet(self.sheet_names["checks"])
        ws.sheet_properties.tabColor = self.colors["primary"]
        ws.views.sheetView[0].showGridLines = False
        counts = self._counts()
        self._title(ws, "Checks — is every record accounted for?", "Each record ends up in exactly one result, so the totals must balance.", 4)

        accounted_1 = counts["matched"] + counts["differ"] + counts["only_1"] + counts["resolved_1"]
        accounted_2 = counts["matched"] + counts["differ"] + counts["only_2"] + counts["resolved_2"]
        resolved_check = [
            ("Not found, explained by an auto-resolution rule", counts["resolved_1"], counts["resolved_2"], "")
        ] if counts["resolved_1"] or counts["resolved_2"] else []
        checks = [
            ("Records compared", counts["total_1"], counts["total_2"], ""),
            ("Matched – all fields agree", counts["matched"], counts["matched"], ""),
            ("Matched – values differ", counts["differ"], counts["differ"], "Pass" if counts["differ"] == 0 else "Review"),
            ("Not found in the other file", counts["only_1"], counts["only_2"], "Pass" if counts["only_1"] + counts["only_2"] == 0 else "Review"),
            *resolved_check,
            (
                "Records accounted for (matched + differ + not found" + (" + auto-resolved)" if resolved_check else ")"),
                accounted_1,
                accounted_2,
                "Pass" if accounted_1 == counts["total_1"] and accounted_2 == counts["total_2"] else "Check",
            ),
        ]
        headers = ["Check", self.file1, self.file2, "Result"]
        border = self._thin_border()
        for col_idx, header in enumerate(headers, start=1):
            cell = ws.cell(row=4, column=col_idx, value=header)
            cell.font = self._font(10, True, self.colors["header_fg"])
            cell.fill = PatternFill("solid", fgColor=self.colors["primary"])
            cell.alignment = Alignment(horizontal="center", vertical="center")
        result_style = {
            "Pass": (self.colors["pass_bg"], self.colors["pass"]),
            "Review": (self.colors["warn_bg"], self.colors["warning"]),
            "Check": (self.colors["fail_bg"], self.colors["exception"]),
        }
        for row_idx, (label, value_1, value_2, result) in enumerate(checks, start=5):
            ws.cell(row=row_idx, column=1, value=label).font = self._font(10)
            for col_idx, value in ((2, value_1), (3, value_2)):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.number_format = "#,##0"
                cell.alignment = Alignment(horizontal="center")
            result_cell = ws.cell(row=row_idx, column=4, value=result or "—")
            result_cell.alignment = Alignment(horizontal="center")
            if result in result_style:
                background, color = result_style[result]
                result_cell.fill = PatternFill("solid", fgColor=background)
                result_cell.font = self._font(10, True, color)
            for col_idx in range(1, 5):
                ws.cell(row=row_idx, column=col_idx).border = border

        row = 5 + len(checks) + 1
        ws.cell(row=row, column=1, value="How matching was done").font = self._font(11, True, self.colors["primary"])
        notes = [f"{label}: {value}" for label, value in self._matching_description(counts)]
        override_rules = [
            rule.get("report_label") or rule.get("sheet_rule_id", "")
            for rule in self.data.get("sheet_rules") or []
            if rule.get("date_only_override")
        ]
        if override_rules:
            notes.append(
                "WARNING: date-only matching was explicitly allowed for "
                f"{', '.join(override_rules)}; transactions on the same day may have been paired incorrectly."
            )
        if self.multi_rule:
            notes.append(f"Multiple sheet rules: each rule's keys and counts are in '{self.sheet_names['rules']}'.")
        for offset, note in enumerate(notes, start=1):
            ws.merge_cells(start_row=row + offset, start_column=1, end_row=row + offset, end_column=4)
            cell = ws.cell(row=row + offset, column=1, value=f"•  {note}")
            warning = note.startswith("WARNING")
            cell.font = self._font(9.5, warning, self.colors["exception"] if warning else self.colors["text_muted"])
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[row + offset].height = 28 if len(note) > 90 else 16

        ws.column_dimensions["A"].width = 52
        ws.column_dimensions["B"].width = 24
        ws.column_dimensions["C"].width = 24
        ws.column_dimensions["D"].width = 14

    # ── 08 Sheet Rules ─────────────────────────────────────────────────────
    def _has_rule_breakdown(self) -> bool:
        """Single-rule reports without special matching keep the shorter tab set."""
        rules = self.data.get("sheet_rules") or []
        return len(rules) > 1 or any(
            rule.get("status") != "completed"
            or rule.get("secondary_conditions")
            or rule.get("date_only_override")
            or rule.get("transformations")
            or rule.get("matching_passes")
            for rule in rules
        )

    def _generate_sheet_rules(self, wb: openpyxl.Workbook) -> None:
        ws = wb.create_sheet(self.sheet_names["rules"])
        ws.sheet_properties.tabColor = self.colors["accent"]
        ws.views.sheetView[0].showGridLines = False

        metric_columns = [
            ("Records in source", "source_records"),
            ("Records in destination", "destination_records"),
            ("Exact matches", "exact_matches"),
            ("Matches to confirm", "exception_matches"),
            ("Of these, matched in later passes", "keyless_matches"),
            ("Several possible matches", "ambiguous_matches"),
            ("Not found", "not_found_matches"),
            ("Values differ", "field_discrepancies"),
            ("Only in source", "only_in_file_1"),
            ("Only in destination", "only_in_file_2"),
        ]
        last_col = get_column_letter(len(metric_columns))
        navy_fill = PatternFill("solid", fgColor=self.colors["primary"])
        accent_fill = PatternFill("solid", fgColor=self.colors["neutral_bg"])
        border = self._thin_border()

        ws.merge_cells(f"A1:{last_col}1")
        ws["A1"] = "Sheet Rules — how each sheet pair was reconciled"
        ws["A1"].font = self._font(13, True, self.colors["primary"])
        ws.merge_cells(f"A2:{last_col}2")
        ws["A2"] = "Every sheet rule is matched with its own keys, conditions and mappings. Counts below are per rule, never shared."
        ws["A2"].font = self._font(9, color=self.colors["text_muted"])

        def describe_sources(workbook: str | None, sheets: list[str]) -> str:
            sheet_text = ", ".join(sheets) if sheets else "Default sheet"
            return f"{workbook} › {sheet_text}" if workbook else sheet_text

        def describe_condition(condition: dict) -> str:
            method = str(condition.get("comparison_method", "")).replace("_", " ")
            if condition.get("comparison_method") == "numeric_tolerance":
                method = f"{method} ±{condition.get('numeric_tolerance') or 0}"
            return f"{condition.get('source_column')} ↔ {condition.get('destination_column')} ({method})"

        def describe_transformation(step: dict) -> str:
            from app.reconciliation_engine.transformations import describe

            side = step.get("side", "both")
            return f"{describe(step)} ({'both files' if side == 'both' else side})"

        def describe_pass(item: dict, number: int) -> str:
            from app.reconciliation_engine.matching.pass_matcher import pass_label

            tolerance = []
            if item.get("amount_tolerance"):
                tolerance.append(f"±{item['amount_tolerance']}")
            if item.get("amount_tolerance_percent"):
                tolerance.append(f"±{item['amount_tolerance_percent']}%")
            return pass_label(item, number) + (f" (amount {' / '.join(tolerance)})" if tolerance else "")

        def describe_similarity(policy: dict) -> str:
            matcher = policy.get("matcher_type_override") or "automatic by data type"
            threshold = policy.get("threshold")
            return f"{matcher}; minimum confidence {f'{threshold}%' if threshold is not None else 'automatic'}"

        row = 4
        for number, rule in enumerate(self.data.get("sheet_rules") or [], start=1):
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(metric_columns))
            source_sheets = ", ".join(rule.get("source_sheets") or []) or "Default sheet"
            destination_sheets = ", ".join(rule.get("destination_sheets") or []) or "Default sheet"
            header = ws.cell(row=row, column=1, value=f"RULE {number} — {source_sheets} ↔ {destination_sheets}")
            header.font = self._font(11, True, self.colors["header_fg"])
            header.fill = navy_fill
            row += 1

            failed = rule.get("status") != "completed"
            details = [
                ("Report label", rule.get("report_label") or ""),
                ("Source", describe_sources(rule.get("source_file"), rule.get("source_sheets") or [])),
                ("Destination", describe_sources(rule.get("destination_file"), rule.get("destination_sheets") or [])),
                (
                    "Primary key",
                    f"{' + '.join(rule.get('primary_key_source') or [])} → {' + '.join(rule.get('primary_key_destination') or [])}",
                ),
                (
                    "Secondary keys (must also match)",
                    "; ".join(describe_condition(condition) for condition in rule.get("secondary_conditions") or []) or "None",
                ),
                ("Primary-key similarity", describe_similarity(rule.get("similarity_policy") or {})),
                (
                    "Date-only override",
                    "ENABLED — date-only keys can match unrelated same-day transactions"
                    if rule.get("date_only_override")
                    else "No",
                ),
                ("Compared fields", rule.get("mapping_count", 0)),
                ("Prepared before matching", "; ".join(describe_transformation(step) for step in rule.get("transformations") or []) or "None"),
                ("Additional matching passes", "; ".join(describe_pass(item, index) for index, item in enumerate(rule.get("matching_passes") or [], start=2)) or "None"),
                ("Date format", "Month first (MM/DD/YYYY)" if rule.get("date_format") == "month_first" else "Day first (DD/MM/YYYY)"),
                ("Status", f"FAILED — {rule.get('error')}" if failed else "Completed"),
            ]
            for label, value in details:
                label_cell = ws.cell(row=row, column=1, value=label)
                label_cell.font = self._font(9, True)
                label_cell.fill = accent_fill
                label_cell.border = border
                ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=len(metric_columns))
                value_cell = ws.cell(row=row, column=2, value=value)
                warn = (label == "Status" and failed) or (label == "Date-only override" and rule.get("date_only_override"))
                value_cell.font = self._font(9, bool(warn), self.colors["exception"] if warn else self.colors["text_dark"])
                value_cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
                value_cell.border = border
                row += 1

            if not failed:
                summary = rule.get("summary") or {}
                for col_idx, (label, key) in enumerate(metric_columns, start=1):
                    head = ws.cell(row=row, column=col_idx, value=label)
                    head.font = self._font(9, True, self.colors["header_fg"])
                    head.fill = PatternFill("solid", fgColor=self.colors["accent"])
                    head.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                    head.border = border
                    value = ws.cell(row=row + 1, column=col_idx, value=summary.get(key, 0))
                    value.font = self._font(10)
                    value.alignment = Alignment(horizontal="center", vertical="center")
                    value.border = border
                ws.row_dimensions[row].height = 30
                row += 2
            row += 1

        ws.column_dimensions["A"].width = 30.0
        for col_idx in range(2, len(metric_columns) + 1):
            ws.column_dimensions[get_column_letter(col_idx)].width = 16.0


def generate_enterprise_report(data: dict, config: dict, output_path: Path) -> None:
    report_config = ReportConfig(**config)
    reporter = UniversalReporter(data, report_config, output_path)
    reporter.generate()
