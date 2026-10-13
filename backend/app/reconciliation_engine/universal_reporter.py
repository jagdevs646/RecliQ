"""Constant-memory Excel reports with complete CSV artifacts for capped tabs."""
from __future__ import annotations

import csv
import itertools
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd
import xlsxwriter

from app.reconciliation_engine.matching.pass_matcher import pass_label
from app.reconciliation_engine.transformations import describe as describe_transformation

EXCEL_MAX_DATA_ROWS = 1_048_572
MATCHED_WORKBOOK_ROW_CAP = 50_000
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


def excel_writer_engine() -> str:
    return "xlsxwriter"


class ReportConfig:
    def __init__(
        self,
        include_summary: bool = True,
        include_exceptions: bool = True,
        include_matched: bool = True,
        include_missing_file_1: bool = True,
        include_missing_file_2: bool = True,
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


@dataclass(frozen=True)
class ReportGeneration:
    capped: bool
    csv_paths: tuple[Path, ...]
    sheet_counts: dict[str, int]


def _clean_file_label(name: str) -> str:
    return re.sub(r"\.(xlsx|xls|csv|tsv)\b", "", str(name).strip(), flags=re.IGNORECASE)


def _transformation_text(item: dict) -> str:
    """A preparation step as the report shows it, naming the file it changed
    unless it applied to both: "Invert sign: Amount (destination)"."""
    detail = describe_transformation(item)
    side = item.get("side", "both")
    return f"{detail} ({side})" if side != "both" else detail


def _visible(column: str) -> bool:
    return column not in _HIDDEN_COLUMNS and not str(column).startswith(_HIDDEN_PREFIXES) and not str(column).upper().startswith("ROW")


def _cell_value(value: Any) -> Any:
    # A missing date (NaT) passes as a datetime but cannot be written as one.
    if value is None or value is pd.NaT or (isinstance(value, np.datetime64) and np.isnat(value)):
        return None
    if isinstance(value, np.datetime64):
        value = pd.Timestamp(value)
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (list, dict, set, tuple)):
        return str(value)
    return value


def _row_number(record: dict, file_number: int) -> Any:
    for key in (f"ROW (FILE {file_number})", f"ROW (File {file_number})"):
        if key in record:
            return record[key]
    return next((value for key, value in record.items() if str(key).upper().startswith("ROW")), None)


class _FilteredRows:
    def __init__(self, rows: Iterable[dict], predicate: Callable[[dict], bool]):
        self.rows = rows
        self.predicate = predicate

    def __iter__(self):
        return (row for row in self.rows if self.predicate(row))

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __bool__(self) -> bool:
        return next(iter(self), None) is not None


class UniversalReporter:
    """Writes report sheets in row order without retaining workbook cells."""

    def __init__(self, data: dict, config: ReportConfig, output_path: Path):
        self.data = data
        self.config = config
        self.output_path = output_path
        metadata = data.get("metadata") or {}
        self.file1 = metadata.get("file_1_name") or "File 1"
        self.file2 = metadata.get("file_2_name") or "File 2"
        self.multi_rule = len(data.get("sheet_rules") or []) > 1
        self.sheet_names = self._sheet_names()
        self.csv_paths: list[Path] = []
        self.sheet_counts: dict[str, int] = {}
        self.capped = False
        self._resolved_counts_cache: dict[str, int] | None = None
        self.included: dict[str, bool] = {}

    def _sheet_names(self) -> dict[str, str]:
        only1 = f"03 Only in {_clean_file_label(self.file1)}"[:31]
        only2 = f"04 Only in {_clean_file_label(self.file2)}"[:31]
        if only1[3:] == only2[3:]:
            only1, only2 = "03 Only in File 1", "04 Only in File 2"
        return {
            "summary": "01 Summary", "differences": "02 Differences", "only_1": only1,
            "only_2": only2, "review": "05 Match Review", "matched": "06 Matched",
            "checks": "07 Checks", "rules": "08 Sheet Rules", "resolved": "09 Auto-resolved",
        }

    def _review_records(self) -> _FilteredRows:
        return _FilteredRows(
            self.data.get("identity_resolution", []),
            lambda row: row.get("IDENTITY CLASSIFICATION") in {"EXCEPTION_MATCH", "AMBIGUOUS_MATCH"},
        )

    def _resolved_counts(self) -> dict[str, int]:
        if self._resolved_counts_cache is None:
            counts = {"only_in_source": 0, "only_in_destination": 0}
            for record in self.data.get("auto_resolved") or []:
                kind = record.get("__KIND__")
                if kind in counts:
                    counts[kind] += 1
            self._resolved_counts_cache = counts
        return self._resolved_counts_cache

    def _counts(self) -> dict[str, int]:
        stats = self.data.get("statistics") or {}
        identity = stats.get("identity") or {}
        return {
            "total_1": int(stats.get("total_file_1", 0) or 0),
            "total_2": int(stats.get("total_file_2", 0) or 0),
            "matched": len(self.data.get("matched_records") or []),
            "differ": int(stats.get("mismatched", len(self.data.get("exceptions") or [])) or 0),
            "only_1": len(self.data.get("missing_in_file_2") or []),
            "only_2": len(self.data.get("missing_in_file_1") or []),
            "review": int(identity.get("EXCEPTION_MATCH", 0) + identity.get("AMBIGUOUS_MATCH", 0)) or len(self._review_records()),
            "resolved": len(self.data.get("auto_resolved") or []),
            "resolved_1": self._resolved_counts()["only_in_source"],
            "resolved_2": self._resolved_counts()["only_in_destination"],
        }

    def _matching_description(self) -> list[tuple[str, str]]:
        metadata = self.data.get("metadata") or {}
        rules = self.data.get("sheet_rules") or []
        if len(rules) > 1:
            description = [("Primary key", f"Each sheet rule uses its own keys; see '{self.sheet_names['rules']}'.")]
        else:
            rule = rules[0] if rules else {}
            source = rule.get("primary_key_source") or metadata.get("matching_keys") or []
            destination = rule.get("primary_key_destination") or []
            key = " + ".join(source) + (f" ↔ {' + '.join(destination)}" if destination else "")
            description = [("Primary key", f"{key or 'Key'} (spacing, case and punctuation differences are ignored)")]
            secondary = metadata.get("secondary_keys") or [
                f"{condition.get('source_column')} ↔ {condition.get('destination_column')}"
                for condition in rule.get("secondary_conditions") or []
            ]
            description.append((
                "Secondary keys",
                f"{'; '.join(secondary)} — must also match for records to be paired" if secondary else "None",
            ))
        if metadata.get("compared_fields"):
            description.append(("Compared fields", ", ".join(metadata["compared_fields"])))
        if metadata.get("transformations"):
            description.append((
                "Prepared before matching",
                "; ".join(metadata["transformations"]) + " (original values are shown next to changed ones)",
            ))
        passes = metadata.get("matching_passes") or []
        if len(passes) > 1 or (passes and passes[0] != "Primary key"):
            pass_counts = metadata.get("pass_counts") or {}
            description.append((
                "Matching passes (in order)",
                " → ".join(f"{label} ({pass_counts.get(label, 0):,} matched)" for label in passes)
                + ". Each pass only looks at records still unmatched; ties are never decided automatically.",
            ))
        normalization = metadata.get("normalization_rules_used") or {}
        if normalization:
            top = sorted(normalization.items(), key=lambda item: -item[1])[:6]
            description.append((
                "Name normalization used",
                ", ".join(f"{rule} ({count:,})" for rule, count in top) + " — spelling variants treated as the same name.",
            ))
        resolutions = metadata.get("resolution_rules_used") or {}
        if resolutions:
            description.append((
                "Auto-resolution rules",
                ", ".join(f"{rule} ({count:,})" for rule, count in sorted(resolutions.items(), key=lambda item: -item[1]))
                + " — explained exceptions remain listed separately.",
            ))
        if metadata.get("tolerance_bands"):
            accepted = metadata.get("tolerance_counts") or {}
            description.append((
                "Tolerance (after matching)",
                "; ".join(metadata["tolerance_bands"])
                + f" — {accepted.get('fields', 0):,} difference(s) accepted; {accepted.get('records', 0):,} record(s) moved to "
                f"'{self.sheet_names['matched']}' with a comment. Larger differences are left as they were.",
            ))
        if metadata.get("date_convention") and str(metadata["date_convention"]).startswith("Month"):
            description.append(("Date format", metadata["date_convention"]))
        grouped = sum(
            1
            for category in ("matched_records", "missing_in_file_1", "missing_in_file_2")
            for record in self.data.get(category) or []
            if record.get("GROUPED ROWS")
        ) + len({repr(row.get("Primary Key")) for row in self.data.get("exceptions") or [] if row.get("Grouped Rows")})
        if grouped:
            description.append(("Combined rows", f"{grouped:,} record(s) were built by combining rows that share a key; see the Combined rows columns."))
        return description

    def _record_keys(self, row: dict) -> tuple[Any, Any]:
        meta = self.data.get("metadata") or {}
        key_label = ", ".join(meta.get("matching_keys") or ["Key"])
        key1 = row.get("COMPOSITE MATCH KEY", row.get("MATCH KEY"))
        if key1 is None:
            key1 = row.get(key_label, row.get(f"{key_label} (File1)", row.get(f"{key_label} (FILE 1)")))
        key2 = row.get("MATCHED COMPOSITE KEY", row.get("MATCHED KEY"))
        if key2 is None:
            key2 = row.get(f"MATCHED {key_label}", row.get(f"{key_label} (File2)", row.get(f"{key_label} (FILE 2)", key1)))
        return key1, key2

    def generate(self) -> ReportGeneration:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        workbook = xlsxwriter.Workbook(self.output_path, {
            "constant_memory": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
            "default_date_format": self.config.date_format,
        })
        self.included = {
            "differences": self.config.include_exceptions,
            "only_1": self.config.include_missing_file_1,
            "only_2": self.config.include_missing_file_2,
            "review": self.config.include_field_differences and bool(self._review_records()),
            "matched": self.config.include_matched,
            "checks": self.config.include_controls,
            "rules": bool(self.data.get("sheet_rules")) and (
                len(self.data.get("sheet_rules") or []) > 1
                or any(
                    rule.get("status") != "completed"
                    or rule.get("secondary_conditions")
                    or rule.get("date_only_override")
                    or rule.get("transformations")
                    or rule.get("matching_passes")
                    or rule.get("tolerances")
                    for rule in self.data.get("sheet_rules") or []
                )
            ),
            "resolved": self.config.include_auto_resolved and bool(self.data.get("auto_resolved")),
        }
        palette = {
            "primary": "#1F3864",
            "accent": "#2E5395",
            "header_fg": "#FFFFFF",
            "pass": "#1E7B34",
            "warning": "#B36A00",
            "exception": "#C0392B",
            "critical": "#A6192E",
            "review": "#6C4AA0",
            "neutral_bg": "#F2F2F2",
            "text_dark": "#000000",
            "text_muted": "#595959",
            "border": "#D9D9D9",
            "pass_bg": "#E3F4E8",
            "warn_bg": "#FFF3D6",
            "fail_bg": "#FADBD8",
            "link": "#1F5FBF",
            "row_alt": "#F7F9FC",
        }
        self.colors = palette
        self.formats = {
            "title": workbook.add_format({"font_name": "Arial", "font_size": 14, "bold": True, "font_color": palette["primary"], "valign": "vcenter"}),
            "summary_title": workbook.add_format({"font_name": "Arial", "font_size": 18, "bold": True, "font_color": palette["header_fg"], "bg_color": palette["primary"], "valign": "vcenter", "indent": 1}),
            "summary_subtitle": workbook.add_format({"font_name": "Arial", "font_size": 10, "font_color": palette["header_fg"], "bg_color": palette["accent"], "valign": "vcenter", "indent": 1}),
            "subtitle": workbook.add_format({"font_name": "Arial", "font_size": 9, "font_color": palette["text_muted"], "text_wrap": True, "valign": "top"}),
            "header": workbook.add_format({"font_name": "Arial", "font_size": 10, "bold": True, "font_color": palette["header_fg"], "bg_color": palette["primary"], "text_wrap": True, "valign": "vcenter", "border": 1, "border_color": palette["border"]}),
            "note": workbook.add_format({"font_name": "Arial", "font_size": 11, "bold": True, "font_color": palette["warning"], "bg_color": palette["warn_bg"], "text_wrap": True, "valign": "vcenter"}),
            "body": workbook.add_format({"font_name": "Arial", "font_size": 10, "font_color": palette["text_dark"], "valign": "vcenter", "bottom": 1, "bottom_color": palette["border"]}),
            "body_alt": workbook.add_format({"font_name": "Arial", "font_size": 10, "font_color": palette["text_dark"], "bg_color": palette["row_alt"], "valign": "vcenter", "bottom": 1, "bottom_color": palette["border"]}),
            "body_number": workbook.add_format({"font_name": "Arial", "font_size": 10, "font_color": palette["text_dark"], "num_format": self.config.number_format, "valign": "vcenter", "bottom": 1, "bottom_color": palette["border"]}),
            "body_number_alt": workbook.add_format({"font_name": "Arial", "font_size": 10, "font_color": palette["text_dark"], "bg_color": palette["row_alt"], "num_format": self.config.number_format, "valign": "vcenter", "bottom": 1, "bottom_color": palette["border"]}),
            "body_percent": workbook.add_format({"font_name": "Arial", "font_size": 10, "font_color": palette["text_dark"], "num_format": "0.00%", "valign": "vcenter", "bottom": 1, "bottom_color": palette["border"]}),
            "body_percent_alt": workbook.add_format({"font_name": "Arial", "font_size": 10, "font_color": palette["text_dark"], "bg_color": palette["row_alt"], "num_format": "0.00%", "valign": "vcenter", "bottom": 1, "bottom_color": palette["border"]}),
            "body_date": workbook.add_format({"font_name": "Arial", "font_size": 10, "font_color": palette["text_dark"], "num_format": self.config.date_format, "valign": "vcenter", "bottom": 1, "bottom_color": palette["border"]}),
            "body_date_alt": workbook.add_format({"font_name": "Arial", "font_size": 10, "font_color": palette["text_dark"], "bg_color": palette["row_alt"], "num_format": self.config.date_format, "valign": "vcenter", "bottom": 1, "bottom_color": palette["border"]}),
            "empty": workbook.add_format({"font_name": "Arial", "font_size": 10, "font_color": palette["text_muted"], "italic": True, "valign": "vcenter"}),
            "section": workbook.add_format({"font_name": "Arial", "font_size": 11, "bold": True, "font_color": palette["primary"], "bottom": 2, "bottom_color": palette["primary"], "valign": "vcenter"}),
            "card_label": workbook.add_format({"font_name": "Arial", "font_size": 9, "bold": True, "font_color": palette["text_muted"], "bg_color": palette["neutral_bg"], "align": "center", "valign": "vcenter", "text_wrap": True}),
            "card_value": workbook.add_format({"font_name": "Arial", "font_size": 20, "bold": True, "font_color": palette["primary"], "bg_color": palette["neutral_bg"], "align": "center", "valign": "vcenter", "num_format": "#,##0"}),
            "card_pass": workbook.add_format({"font_name": "Arial", "font_size": 20, "bold": True, "font_color": palette["pass"], "bg_color": palette["neutral_bg"], "align": "center", "valign": "vcenter", "num_format": "#,##0"}),
            "card_warning": workbook.add_format({"font_name": "Arial", "font_size": 20, "bold": True, "font_color": palette["warning"], "bg_color": palette["neutral_bg"], "align": "center", "valign": "vcenter", "num_format": "#,##0"}),
            "card_rate_pass": workbook.add_format({"font_name": "Arial", "font_size": 20, "bold": True, "font_color": palette["pass"], "bg_color": palette["neutral_bg"], "align": "center", "valign": "vcenter", "num_format": "0.00%"}),
            "card_rate_warning": workbook.add_format({"font_name": "Arial", "font_size": 20, "bold": True, "font_color": palette["warning"], "bg_color": palette["neutral_bg"], "align": "center", "valign": "vcenter", "num_format": "0.00%"}),
            "result_pass": workbook.add_format({"font_name": "Arial", "font_size": 10, "bold": True, "font_color": palette["pass"], "valign": "vcenter", "text_wrap": True}),
            "result_warning": workbook.add_format({"font_name": "Arial", "font_size": 10, "bold": True, "font_color": palette["warning"], "valign": "vcenter", "text_wrap": True}),
            "result_exception": workbook.add_format({"font_name": "Arial", "font_size": 10, "bold": True, "font_color": palette["exception"], "valign": "vcenter", "text_wrap": True}),
            "result_critical": workbook.add_format({"font_name": "Arial", "font_size": 10, "bold": True, "font_color": palette["critical"], "valign": "vcenter", "text_wrap": True}),
            "result_review": workbook.add_format({"font_name": "Arial", "font_size": 10, "bold": True, "font_color": palette["review"], "valign": "vcenter", "text_wrap": True}),
            "result_accent": workbook.add_format({"font_name": "Arial", "font_size": 10, "bold": True, "font_color": palette["accent"], "valign": "vcenter", "text_wrap": True}),
            "result_count": workbook.add_format({"font_name": "Arial", "font_size": 11, "bold": True, "align": "center", "valign": "vcenter", "num_format": "#,##0", "border": 1, "border_color": palette["border"]}),
            "result_meaning": workbook.add_format({"font_name": "Arial", "font_size": 9, "font_color": palette["text_muted"], "text_wrap": True, "valign": "vcenter", "border": 1, "border_color": palette["border"]}),
            "result_link": workbook.add_format({"font_name": "Arial", "font_size": 9, "font_color": palette["link"], "underline": True, "align": "center", "valign": "vcenter", "text_wrap": True, "border": 1, "border_color": palette["border"]}),
            "result_no_link": workbook.add_format({"font_name": "Arial", "font_size": 9, "font_color": palette["text_muted"], "align": "center", "valign": "vcenter", "border": 1, "border_color": palette["border"]}),
            "body_label": workbook.add_format({"font_name": "Arial", "font_size": 10, "bold": True, "valign": "vcenter", "text_wrap": True}),
            "body_value": workbook.add_format({"font_name": "Arial", "font_size": 10, "valign": "vcenter", "text_wrap": True}),
            "body_value_muted": workbook.add_format({"font_name": "Arial", "font_size": 10, "font_color": palette["text_muted"], "valign": "vcenter", "text_wrap": True}),
            "body_border": workbook.add_format({"font_name": "Arial", "font_size": 10, "valign": "vcenter", "border": 1, "border_color": palette["border"]}),
            "body_count": workbook.add_format({"font_name": "Arial", "font_size": 10, "valign": "vcenter", "align": "center", "num_format": "#,##0", "border": 1, "border_color": palette["border"]}),
            "body_share": workbook.add_format({"font_name": "Arial", "font_size": 10, "valign": "vcenter", "align": "center", "num_format": "0.0%", "border": 1, "border_color": palette["border"]}),
        }
        try:
            self._write_summary(workbook)
            for sheet in self._detail_sheets():
                self._write_detail(workbook, *sheet)
        finally:
            workbook.close()
        return ReportGeneration(self.capped, tuple(self.csv_paths), dict(self.sheet_counts))

    def _write_summary(self, workbook) -> None:
        if not self.config.include_summary:
            return
        ws = workbook.add_worksheet(self.sheet_names["summary"])
        ws.hide_gridlines(2)
        ws.set_tab_color(self.colors["primary"])
        ws.set_column("A:A", 2)
        ws.set_column("B:B", 32)
        ws.set_column("C:G", 13)
        ws.set_column("H:H", 22)
        ws.set_column("I:I", 3)
        counts = self._counts()
        ws.merge_range("B1:H1", "Reconciliation Report", self.formats["summary_title"])
        ws.set_row(0, 34)
        ws.merge_range(
            "B2:H2",
            f"{self.file1}  compared with  {self.file2}   ·   Generated {datetime.now().strftime('%d %b %Y')}",
            self.formats["summary_subtitle"],
        )
        ws.set_row(1, 20)
        attention = counts["differ"] + counts["only_1"] + counts["only_2"]
        if attention == 0 and counts["review"] == 0:
            status = f"All {counts['total_1']:,} records match. No action needed."
            status_format = workbook.add_format({
                "font_name": "Arial", "font_size": 12, "bold": True, "font_color": self.colors["pass"],
                "bg_color": self.colors["pass_bg"], "valign": "vcenter", "indent": 1,
            })
        else:
            status = f"{attention:,} item{'s' if attention != 1 else ''} need your attention — see the Results table below."
            status_format = workbook.add_format({
                "font_name": "Arial", "font_size": 12, "bold": True, "font_color": self.colors["warning"],
                "bg_color": self.colors["warn_bg"], "valign": "vcenter", "indent": 1,
            })
        ws.merge_range("B4:H4", status, status_format)
        ws.set_row(3, 26)

        denominator = counts["matched"] + counts["differ"] + counts["only_1"] + counts["only_2"] + counts["resolved"]
        agreement_rate = counts["matched"] / denominator if denominator else 0
        rate_format = "card_rate_pass" if agreement_rate >= 0.99 else "card_rate_warning"
        results = [
            ("Matched – all fields agree", counts["matched"], "Found in both files and every compared field is the same.", "matched", "pass"),
            ("Matched – values differ", counts["differ"], "Found in both files, but at least one compared field is different.", "differences", "exception"),
            (f"Only in {self.file1}", counts["only_1"], f"In {self.file1} but no matching record was found in {self.file2}.", "only_1", "critical"),
            (f"Only in {self.file2}", counts["only_2"], f"In {self.file2} but no matching record was found in {self.file1}.", "only_2", "critical"),
        ]
        if counts["review"]:
            results.append(("Needs review", counts["review"], "Matched through secondary keys or several possible matches were found; confirm these records.", "review", "review"))
        if counts["resolved"]:
            results.append(("Auto-resolved by rules", counts["resolved"], "Exceptions explained by your configured rules; details remain available on the report tab.", "resolved", "accent"))

        cards = [
            ("B6:B6", "B7:B7", f"Records in {self.file1}", counts["total_1"], "card_value"),
            ("C6:D6", "C7:D7", f"Records in {self.file2}", counts["total_2"], "card_value"),
            ("E6:F6", "E7:F7", "Matched, no differences", counts["matched"], "card_pass"),
            ("G6:H6", "G7:H7", "Exact agreement rate", agreement_rate, rate_format),
        ]
        for label_range, _value_range, label, _value, _value_format in cards:
            label_start, label_end = label_range.split(":")
            if label_start == label_end:
                ws.write(label_start, label, self.formats["card_label"])
            else:
                ws.merge_range(label_range, label, self.formats["card_label"])
        for _label_range, value_range, _label, value, value_format in cards:
            value_start, value_end = value_range.split(":")
            if value_start == value_end:
                ws.write(value_start, value, self.formats[value_format])
            else:
                ws.merge_range(value_range, value, self.formats[value_format])
        ws.set_row(5, 28)
        ws.set_row(6, 34)

        row = self._summary_section(ws, 8, "RESULTS")
        ws.merge_range(row, 3, row, 6, "What it means", workbook.add_format({
            "font_name": "Arial", "font_size": 10, "bold": True, "font_color": self.colors["header_fg"],
            "bg_color": self.colors["primary"], "align": "left", "valign": "vcenter",
        }))
        for column, text, align in ((1, "Result", "left"), (2, "Records", "center"), (7, "Where to look", "center")):
            fmt = workbook.add_format({
                "font_name": "Arial", "font_size": 10, "bold": True, "font_color": self.colors["header_fg"],
                "bg_color": self.colors["primary"], "align": align, "valign": "vcenter",
            })
            ws.write(row, column, text, fmt)
        ws.set_row(row, 22)
        first_result_row = row + 1
        tolerance_counts = (self.data.get("metadata") or {}).get("tolerance_counts") or {}
        within_tolerance = int(tolerance_counts.get("records", 0) or 0) or sum(
            1 for record in self.data.get("matched_records") or [] if record.get("WITHIN TOLERANCE")
        )
        for label, count, meaning, sheet_key, color_key in results:
            row += 1
            if sheet_key == "matched" and within_tolerance:
                meaning += f" Includes {within_tolerance:,} whose differences are within your tolerance; see the Comment column."
            ws.write(row, 1, label, self.formats[f"result_{color_key}"])
            ws.write_number(row, 2, count, self.formats["result_count"])
            ws.merge_range(row, 3, row, 6, meaning, self.formats["result_meaning"])
            if self.included.get(sheet_key):
                sheet_name = self.sheet_names[sheet_key].replace("'", "''")
                ws.write_url(row, 7, f"internal:'{sheet_name}'!A1", self.formats["result_link"], f"Open '{self.sheet_names[sheet_key]}'")
            else:
                ws.write(row, 7, "—", self.formats["result_no_link"])
            ws.set_row(row, 30)
        last_result_row = row

        row = self._summary_section(ws, last_result_row + 2, "HOW RECORDS WERE MATCHED")
        for label, value in self._matching_description():
            ws.write(row, 1, label, self.formats["body_label"])
            ws.merge_range(row, 2, row, 7, value, self.formats["body_value"])
            ws.set_row(row, 30 if len(str(value)) > 80 else 18)
            row += 1

        row = self._summary_section(ws, row + 1, "FIELDS WITH DIFFERENCES")
        field_counts = self._field_difference_counts()
        if not field_counts:
            ws.write(row, 1, "No field differences were found.", self.formats["body_value_muted"])
            row += 1
        else:
            ws.write(row, 1, "Field", self.formats["header"])
            ws.write(row, 2, "Records", self.formats["header"])
            ws.merge_range(row, 3, row, 4, "Share of matched records", self.formats["header"])
            ws.set_row(row, 24)
            for field, count in field_counts[:15]:
                row += 1
                ws.write(row, 1, field, self.formats["body_border"])
                ws.write_number(row, 2, count, self.formats["body_count"])
                ws.merge_range(row, 3, row, 4, count / (counts["matched"] + counts["differ"]) if counts["matched"] + counts["differ"] else 0, self.formats["body_share"])
                ws.set_row(row, 20)
            row += 1

        row = self._summary_section(ws, row + 1, "HOW TO READ THIS REPORT")
        guide = [
            f"{self.sheet_names['differences']}: one row per field that differs on a matched record, with both values side by side.",
            f"{self.sheet_names['only_1']} / {self.sheet_names['only_2']}: records without a partner in the other file, and why.",
            f"{self.sheet_names['review']}: secondary-key or ambiguous matches that need confirmation.",
            f"{self.sheet_names['matched']}: records that agree on every compared field.",
            f"{self.sheet_names['checks']}: confirms every record is accounted for.",
        ]
        if counts["resolved"]:
            guide.append(f"{self.sheet_names['resolved']}: exceptions your auto-resolution rules explained, and which rule did it.")
        for line in guide:
            ws.merge_range(row, 1, row, 7, f"•  {line}", self.formats["body_value_muted"])
            ws.set_row(row, 18)
            row += 1

        chart = workbook.add_chart({"type": "bar"})
        colors = [self.colors[item[4]] for item in results]
        chart.add_series({
            "name": "Records",
            "categories": [self.sheet_names["summary"], first_result_row, 1, last_result_row, 1],
            "values": [self.sheet_names["summary"], first_result_row, 2, last_result_row, 2],
            "points": [{"fill": {"color": color}, "border": {"color": color}} for color in colors],
            "data_labels": {"value": True},
        })
        chart.set_title({"name": "Records by result"})
        chart.set_legend({"none": True})
        chart.set_size({"width": 560, "height": 300})
        chart.set_y_axis({"reverse": True})
        chart.set_x_axis({"major_gridlines": {"visible": False}})
        ws.insert_chart("J6", chart)
        ws.activate()

    def _summary_section(self, worksheet, row: int, title: str) -> int:
        worksheet.merge_range(row, 1, row, 7, title, self.formats["section"])
        worksheet.set_row(row, 22)
        return row + 1

    def _field_difference_counts(self) -> list[tuple[str, int]]:
        records_by_field: dict[str, set[tuple[str, str]]] = {}
        for record in self.data.get("exceptions") or []:
            key = record.get("Primary Key", record.get("Match Key"))
            identity = (str(record.get("Sheet Pair", "")), repr(_cell_value(key)))
            records_by_field.setdefault(str(record.get("Field", "")), set()).add(identity)
        return sorted(((field, len(keys)) for field, keys in records_by_field.items()), key=lambda item: (-item[1], item[0]))

    def _detail_sheets(self):
        records = self.data.get("exceptions") or []
        has_matched_key = any("Matched Key" in row for row in records)
        has_secondary = any(row.get("Secondary Key") for row in records)
        has_groups = any(row.get("Grouped Rows") for row in records)
        has_similarity = any(row.get("Similarity") for row in records)
        has_comment = any(row.get("Comment") for row in records)
        headers = (["Sheet"] if self.multi_rule else []) + [f"Key in {self.file1}"]
        headers += [f"Key in {self.file2}"] if has_matched_key else []
        headers += ["Secondary key"] if has_secondary else []
        headers += ["Field", f"{self.file1} value", f"{self.file2} value", "Difference", "Difference %"]
        headers += ["Similarity"] if has_similarity else []
        headers += ["Combined rows"] if has_groups else []
        headers += ["Comment"] if has_comment else []
        headers.append("Reviewer notes")

        def difference_rows():
            for record in records:
                values = [str(record.get("Sheet Pair", "")).strip("[]")] if self.multi_rule else []
                values.append(record.get("Primary Key", record.get("Match Key")))
                if has_matched_key:
                    values.append(record.get("Matched Key"))
                if has_secondary:
                    values.append(record.get("Secondary Key"))
                values += [record.get("Field"), record.get("File 1 Value"), record.get("File 2 Value"), record.get("Difference"), record.get("Difference %")]
                if has_similarity:
                    values.append(record.get("Similarity"))
                if has_groups:
                    values.append(record.get("Grouped Rows"))
                if has_comment:
                    values.append(record.get("Comment"))
                values.append(None)
                yield values

        if self.config.include_exceptions:
            yield ("differences", self.sheet_names["differences"], "Differences — matched records whose values disagree", f"Each row is one field that differs. A record with several differing fields appears once per field. Difference = {self.file1} value − {self.file2} value.", headers, difference_rows, len(records), EXCEL_MAX_DATA_ROWS, "No differences were found. Every matched record agrees on all compared fields.")

        for file_number, enabled, category in (
            (1, self.config.include_missing_file_1, "missing_in_file_2"),
            (2, self.config.include_missing_file_2, "missing_in_file_1"),
        ):
            if not enabled:
                continue
            missing = self.data.get(category) or []
            this_file, other_file = (self.file1, self.file2) if file_number == 1 else (self.file2, self.file1)
            data_columns = list(dict.fromkeys(str(key) for record in missing for key in record if _visible(str(key))))
            has_result = any(record.get("IDENTITY CLASSIFICATION") for record in missing)
            has_reason = any(record.get("MATCH EXPLANATION") for record in missing)
            has_groups = any(record.get("GROUPED ROWS") for record in missing)
            missing_headers = (["Sheet"] if self.multi_rule else []) + data_columns
            missing_headers += ["Result"] if has_result else []
            missing_headers += ["Why it was not matched"] if has_reason else []
            missing_headers += ["Combined rows"] if has_groups else []
            missing_headers.append(f"Row in {this_file}")

            def missing_rows(records=missing, cols=data_columns, result=has_result, reason=has_reason, groups=has_groups, side=file_number):
                for record in records:
                    values = [str(record.get("Sheet Pair", "")).strip("[]")] if self.multi_rule else []
                    values += [record.get(column) for column in cols]
                    if result:
                        values.append(_RESULT_LABELS.get(record.get("IDENTITY CLASSIFICATION"), record.get("IDENTITY CLASSIFICATION")))
                    if reason:
                        values.append(record.get("MATCH EXPLANATION"))
                    if groups:
                        values.append(record.get("GROUPED ROWS"))
                    values.append(_row_number(record, side))
                    yield values

            yield (f"only_{file_number}", self.sheet_names[f"only_{file_number}"], f"Only in {this_file}", f"These records are in {this_file}, but no record in {other_file} has the same key{' and secondary keys' if (self.data.get('metadata') or {}).get('secondary_keys') else ''}.", missing_headers, missing_rows, len(missing), EXCEL_MAX_DATA_ROWS, f"Every record in {this_file} was matched.")

        review = self._review_records()
        if self.config.include_field_differences and review:
            review_headers = (["Sheet"] if self.multi_rule else []) + ["Result", f"Key in {self.file1}", f"Closest key in {self.file2}", "Explanation", "Key similarity", f"Row in {self.file1}"]

            def review_rows():
                for record in review:
                    exception = record.get("IDENTITY CLASSIFICATION") == "EXCEPTION_MATCH"
                    keyless = record.get("MATCH PASS") and record.get("MATCH PASS") != "Primary key"
                    result = f"Matched by {record['MATCH PASS'].split(': ', 1)[-1].lower()}" if keyless and exception else _RESULT_LABELS.get(record.get("IDENTITY CLASSIFICATION"))
                    values = [str(record.get("Sheet Pair", "")).strip("[]")] if self.multi_rule else []
                    values += [result, record.get("MATCH KEY"), record.get("CANDIDATE KEY") or None, record.get("MATCH EXPLANATION"), record.get("MATCH CONFIDENCE") if exception else None, _row_number(record, 1)]
                    yield values

            yield ("review", self.sheet_names["review"], "Match Review — please confirm these", "'Matched by secondary keys': the primary key differed slightly, but every secondary key matched one record. 'Several possible matches': more than one record qualified, so none was chosen automatically.", review_headers, review_rows, len(review), EXCEL_MAX_DATA_ROWS, "Nothing needs review.")

        matched = self.data.get("matched_records") or []
        if self.config.include_matched:
            has_secondary = any(row.get("SECONDARY KEY") for row in matched)
            has_groups = any(row.get("GROUPED ROWS") for row in matched)
            has_normalization = any(row.get("NORMALIZATION APPLIED") for row in matched)
            has_tolerance = any(row.get("WITHIN TOLERANCE") for row in matched)
            matched_headers = (["Sheet"] if self.multi_rule else []) + [f"Key in {self.file1}", f"Key in {self.file2}"]
            matched_headers += ["Secondary key"] if has_secondary else []
            matched_headers.append("How matched")
            matched_headers += ["Normalization applied"] if has_normalization else []
            matched_headers += ["Combined rows"] if has_groups else []
            matched_headers += ["Comment"] if has_tolerance else []
            matched_headers += [f"Row in {self.file1}", f"Row in {self.file2}"]

            def matched_rows():
                for record in matched:
                    key1, key2 = self._record_keys(record)
                    values = [str(record.get("Sheet Pair", "")).strip("[]")] if self.multi_rule else []
                    values += [key1, key2]
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
                    if has_tolerance:
                        note = record.get("WITHIN TOLERANCE")
                        values.append(f"Matched within tolerance: {note}" if note else None)
                    values += [_row_number(record, 1), _row_number(record, 2)]
                    yield values

            matched_subtitle = "No action needed. Listed so every record can be traced back to its row in each file."
            if has_tolerance:
                matched_subtitle += " Records whose only differences are within your tolerance are included; the Comment column says which and why."
            yield ("matched", self.sheet_names["matched"], "Matched — records that agree on every compared field", matched_subtitle, matched_headers, matched_rows, len(matched), MATCHED_WORKBOOK_ROW_CAP, "No record matched on every compared field.")

        if self.config.include_controls:
            checks = self._checks()
            yield ("checks", self.sheet_names["checks"], "Checks — is every record accounted for?", "Each record ends up in exactly one result, so the totals must balance.", ["Check", self.file1, self.file2, "Result"], lambda: iter(checks), len(checks), EXCEL_MAX_DATA_ROWS, "No checks are available.")

        rules = self.data.get("sheet_rules") or []
        if rules and (len(rules) > 1 or any(rule.get("status") != "completed" or rule.get("secondary_conditions") or rule.get("date_only_override") or rule.get("transformations") or rule.get("matching_passes") or rule.get("tolerances") for rule in rules)):
            rule_headers = [
                "Rule", "Report label", "Source", "Destination", "Primary key", "Compared fields", "Transformations",
                "Matching passes", "Status", "Records in source", "Records in destination", "Exact matches",
                "Matches to confirm", "Of these, matched in later passes", "Several possible matches", "Not found",
                "Values differ", "Only source", "Only destination",
            ]
            def rule_rows():
                for index, rule in enumerate(rules, start=1):
                    transformations = [_transformation_text(item) for item in rule.get("transformations") or []]
                    passes = rule.get("matching_passes") or []
                    pass_start = 2 if rule.get("primary_key_source") else 1
                    pass_details = "; ".join(pass_label(item, number) for number, item in enumerate(passes, start=pass_start))
                    summary = rule.get("summary") or {}
                    yield [
                        index, rule.get("report_label"), rule.get("source_file"), rule.get("destination_file"),
                        " + ".join(rule.get("primary_key_source") or []), rule.get("mapping_count", 0),
                        "; ".join(transformations), pass_details, rule.get("status"),
                        summary.get("source_records", 0), summary.get("destination_records", 0),
                        summary.get("exact_matches", 0), summary.get("exception_matches", 0),
                        summary.get("keyless_matches", 0), summary.get("ambiguous_matches", 0),
                        summary.get("not_found_matches", 0), summary.get("field_discrepancies", summary.get("report_rows", 0)),
                        summary.get("only_in_file_1", 0), summary.get("only_in_file_2", 0),
                    ]
            yield ("rules", self.sheet_names["rules"], "Sheet Rules", "Each file and sheet pair uses its own matching and comparison rules.", rule_headers, rule_rows, len(rules), EXCEL_MAX_DATA_ROWS, "No sheet rules were recorded.")

        resolved = self.data.get("auto_resolved") or []
        if self.config.include_auto_resolved and resolved:
            fixed = ["Exception", "Resolution", "Rule", "Rule Version", "Reason Code", "GL Account", "Record"]
            optional = ["Field", "File 1 Value", "File 2 Value", "Difference", "Matched With", "How Matched", "Match Confidence"]
            present = [column for column in optional if any(row.get(column) not in (None, "") for row in resolved)]
            skip = set(fixed) | set(optional) | {"Note"}
            extra = list(dict.fromkeys(key for record in resolved for key in record if key not in skip and _visible(str(key))))
            resolved_headers = (["Sheet"] if self.multi_rule else []) + fixed + present + extra + ["Note", "Row"]

            def resolved_rows():
                for record in resolved:
                    values = [str(record.get("Sheet Pair", "")).strip("[]")] if self.multi_rule else []
                    values += [record.get(column) for column in fixed + present + extra]
                    kind = record.get("__KIND__")
                    values += [record.get("Note"), _row_number(record, 2 if kind == "only_in_destination" else 1)]
                    yield values

            yield ("resolved", self.sheet_names["resolved"], "Auto-resolved — explained by your rules", "These exceptions matched an auto-resolution rule. They are not deleted: each shows the rule and version that explained it, the resolution, reason code and GL account. Rules never decide between several possible matches.", resolved_headers, resolved_rows, len(resolved), EXCEL_MAX_DATA_ROWS, "No exception was auto-resolved.")

    def _checks(self) -> list[list[Any]]:
        counts = self._counts()
        accounted_1 = counts["matched"] + counts["differ"] + counts["only_1"] + counts["resolved_1"]
        accounted_2 = counts["matched"] + counts["differ"] + counts["only_2"] + counts["resolved_2"]
        resolved_check = [[
            "Not found, explained by an auto-resolution rule", counts["resolved_1"], counts["resolved_2"], "",
        ]] if counts["resolved_1"] or counts["resolved_2"] else []
        return [
            ["Records compared", counts["total_1"], counts["total_2"], ""],
            ["Matched – all fields agree", counts["matched"], counts["matched"], ""],
            ["Matched – values differ", counts["differ"], counts["differ"], "Pass" if counts["differ"] == 0 else "Review"],
            ["Not found in the other file", counts["only_1"], counts["only_2"], "Pass" if counts["only_1"] + counts["only_2"] == 0 else "Review"],
            *resolved_check,
            ["Records accounted for (matched + differ + not found" + (" + auto-resolved)" if resolved_check else ")"), accounted_1, accounted_2,
             "Pass" if accounted_1 == counts["total_1"] and accounted_2 == counts["total_2"] else "Check"],
        ]

    def _write_detail(self, workbook, key, sheet_name, title, subtitle, headers, rows_factory: Callable[[], Iterable[list[Any]]], total, cap, empty_message):
        if key == "checks":
            self._write_checks_sheet(workbook, sheet_name, title, subtitle, rows_factory, total)
            return
        if key == "rules":
            self._write_rules_sheet(workbook, sheet_name)
            return

        ws = workbook.add_worksheet(sheet_name[:31])
        ws.hide_gridlines(2)
        tab_colors = {
            "differences": self.colors["exception"],
            "only_1": self.colors["critical"],
            "only_2": self.colors["critical"],
            "review": self.colors["review"],
            "matched": self.colors["pass"],
            "checks": self.colors["primary"],
            "rules": self.colors["accent"],
            "resolved": self.colors["accent"],
        }
        ws.set_tab_color(tab_colors.get(key, self.colors["primary"]))
        ws.set_row(0, 22)
        ws.set_row(1, 30)
        ws.set_default_row(18)
        if len(headers) > 1:
            ws.merge_range(0, 0, 0, len(headers) - 1, title, self.formats["title"])
            ws.merge_range(1, 0, 1, len(headers) - 1, subtitle, self.formats["subtitle"])
        else:
            ws.write(0, 0, title, self.formats["title"])
            ws.write(1, 0, subtitle, self.formats["subtitle"])
        row_cap = min(cap, EXCEL_MAX_DATA_ROWS)
        written_count = min(total, row_cap)
        is_capped = total > row_cap
        if is_capped:
            self.capped = True
            if len(headers) > 1:
                ws.merge_range(2, 0, 2, len(headers) - 1, f"Showing {written_count:,} of {total:,} rows. Download the ZIP for the complete CSV.", self.formats["note"])
            else:
                ws.write(2, 0, f"Showing {written_count:,} of {total:,} rows. Download the ZIP for the complete CSV.", self.formats["note"])
        for column, header in enumerate(headers):
            ws.write(3, column, header, self.formats["header"])
        ws.set_row(3, 30)
        sample = list(itertools.islice(rows_factory(), 100))
        for column, header in enumerate(headers):
            width = max(11, len(str(header)) + 2)
            for sample_row in sample:
                if column < len(sample_row) and sample_row[column] is not None:
                    width = max(width, len(str(sample_row[column])) + 2)
            limit = 90 if header in {"Why it was not matched", "Explanation"} else 50
            width = min(width, limit)
            ws.set_column(column, column, width)
        percent_columns = {index for index, header in enumerate(headers) if str(header).strip().lower() in {"difference %", "match %"}}
        for row_index, values in enumerate(itertools.islice(rows_factory(), written_count), start=4):
            alternate = row_index % 2 == 1
            ws.set_row(row_index, 18)
            for column in range(len(headers)):
                value = _cell_value(values[column] if column < len(values) else None)
                body_format = "body_alt" if alternate else "body"
                if column in percent_columns and isinstance(value, (int, float)):
                    body_format = "body_percent_alt" if alternate else "body_percent"
                    ws.write_number(row_index, column, value, self.formats[body_format])
                elif isinstance(value, bool):
                    ws.write_boolean(row_index, column, value, self.formats[body_format])
                elif isinstance(value, (int, float)):
                    if isinstance(value, float) and not value.is_integer():
                        body_format = "body_number_alt" if alternate else "body_number"
                    ws.write_number(row_index, column, value, self.formats[body_format])
                elif isinstance(value, datetime):
                    body_format = "body_date_alt" if alternate else "body_date"
                    ws.write_datetime(row_index, column, value, self.formats[body_format])
                elif value is None:
                    ws.write_blank(row_index, column, None, self.formats[body_format])
                else:
                    # XlsxWriter stores strings as literal cells; formulas and URLs are disabled.
                    ws.write_string(row_index, column, str(value), self.formats[body_format])
        if total == 0:
            ws.write(4, 0, empty_message, self.formats["empty"])
            ws.set_row(4, 22)
        ws.freeze_panes(4, 0)
        ws.autofilter(3, 0, max(3, 3 + written_count), max(0, len(headers) - 1))
        self.sheet_counts[sheet_name] = total
        if is_capped:
            csv_path = self.output_path.with_name(f"{self.output_path.stem}_{key}_full.csv")
            with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(headers)
                for values in rows_factory():
                    writer.writerow([self._csv_value(values[index] if index < len(values) else None) for index in range(len(headers))])
            self.csv_paths.append(csv_path)

    def _write_checks_sheet(self, workbook, sheet_name: str, title: str, subtitle: str, rows_factory, total: int) -> None:
        ws = workbook.add_worksheet(sheet_name[:31])
        ws.hide_gridlines(2)
        ws.set_tab_color(self.colors["primary"])
        ws.set_column("A:A", 52)
        ws.set_column("B:C", 24)
        ws.set_column("D:D", 14)
        ws.set_row(0, 22)
        ws.set_row(1, 30)
        ws.set_default_row(18)
        ws.merge_range("A1:D1", title, self.formats["title"])
        ws.merge_range("A2:D2", subtitle, self.formats["subtitle"])
        headers = ["Check", self.file1, self.file2, "Result"]
        for column, header in enumerate(headers):
            ws.write(3, column, header, self.formats["header"])
        ws.set_row(3, 30)

        status_formats = {
            "Pass": workbook.add_format({"font_name": "Arial", "font_size": 10, "bold": True, "font_color": self.colors["pass"], "bg_color": self.colors["pass_bg"], "align": "center", "valign": "vcenter", "border": 1, "border_color": self.colors["border"]}),
            "Review": workbook.add_format({"font_name": "Arial", "font_size": 10, "bold": True, "font_color": self.colors["warning"], "bg_color": self.colors["warn_bg"], "align": "center", "valign": "vcenter", "border": 1, "border_color": self.colors["border"]}),
            "Check": workbook.add_format({"font_name": "Arial", "font_size": 10, "bold": True, "font_color": self.colors["exception"], "bg_color": self.colors["fail_bg"], "align": "center", "valign": "vcenter", "border": 1, "border_color": self.colors["border"]}),
        }
        check_format = workbook.add_format({"font_name": "Arial", "font_size": 10, "valign": "vcenter", "border": 1, "border_color": self.colors["border"]})
        number_format = workbook.add_format({"font_name": "Arial", "font_size": 10, "num_format": "#,##0", "align": "center", "valign": "vcenter", "border": 1, "border_color": self.colors["border"]})
        for row_index, values in enumerate(rows_factory(), start=4):
            ws.set_row(row_index, 20)
            ws.write(row_index, 0, values[0], check_format)
            ws.write_number(row_index, 1, values[1], number_format)
            ws.write_number(row_index, 2, values[2], number_format)
            result = values[3] or "—"
            ws.write(row_index, 3, result, status_formats.get(result, check_format))

        note_row = 4 + total + 1
        ws.merge_range(note_row, 0, note_row, 3, "How matching was done", self.formats["section"])
        notes = [f"{label}: {value}" for label, value in self._matching_description()]
        override_rules = [
            rule.get("report_label") or rule.get("sheet_rule_id", "")
            for rule in self.data.get("sheet_rules") or []
            if rule.get("date_only_override")
        ]
        if override_rules:
            notes.append("WARNING: date-only matching was explicitly allowed for " + ", ".join(override_rules) + "; transactions on the same day may have been paired incorrectly.")
        if self.multi_rule:
            notes.append(f"Multiple sheet rules: each rule's keys and counts are in '{self.sheet_names['rules']}'.")
        for row_index, note in enumerate(notes, start=note_row + 1):
            warning_format = workbook.add_format({"font_name": "Arial", "font_size": 9, "bold": True, "font_color": self.colors["exception"], "text_wrap": True, "valign": "top"}) if note.startswith("WARNING") else self.formats["body_value_muted"]
            ws.merge_range(row_index, 0, row_index, 3, f"•  {note}", warning_format)
            ws.set_row(row_index, 28 if len(note) > 90 else 16)
        ws.freeze_panes(4, 0)
        self.sheet_counts[sheet_name] = total

    def _write_rules_sheet(self, workbook, sheet_name: str) -> None:
        rules = self.data.get("sheet_rules") or []
        metrics = [
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
        if any(rule.get("tolerances") for rule in rules):
            metrics.insert(8, ("Matched within tolerance", "within_tolerance_records"))
        last_column = len(metrics) - 1
        ws = workbook.add_worksheet(sheet_name[:31])
        ws.hide_gridlines(2)
        ws.set_tab_color(self.colors["accent"])
        ws.set_row(0, 24)
        ws.set_row(1, 32)
        ws.merge_range(0, 0, 0, last_column, "Sheet Rules — how each sheet pair was reconciled", self.formats["title"])
        ws.merge_range(1, 0, 1, last_column, "Every sheet rule is matched with its own keys, conditions and mappings. Counts below are per rule, never shared.", self.formats["subtitle"])

        banner_format = workbook.add_format({"font_name": "Arial", "font_size": 11, "bold": True, "font_color": self.colors["header_fg"], "bg_color": self.colors["primary"], "valign": "vcenter"})
        label_format = workbook.add_format({"font_name": "Arial", "font_size": 9, "bold": True, "bg_color": self.colors["neutral_bg"], "valign": "vcenter", "text_wrap": True, "border": 1, "border_color": self.colors["border"]})
        value_format = workbook.add_format({"font_name": "Arial", "font_size": 9, "valign": "vcenter", "text_wrap": True, "border": 1, "border_color": self.colors["border"]})
        warning_value_format = workbook.add_format({"font_name": "Arial", "font_size": 9, "bold": True, "font_color": self.colors["exception"], "valign": "vcenter", "text_wrap": True, "border": 1, "border_color": self.colors["border"]})
        metric_header = workbook.add_format({"font_name": "Arial", "font_size": 9, "bold": True, "font_color": self.colors["header_fg"], "bg_color": self.colors["accent"], "text_wrap": True, "align": "center", "valign": "vcenter", "border": 1, "border_color": self.colors["border"]})
        metric_value = workbook.add_format({"font_name": "Arial", "font_size": 10, "align": "center", "valign": "vcenter", "num_format": "#,##0", "border": 1, "border_color": self.colors["border"]})

        def source_description(workbook_name: str | None, sheets: list[str]) -> str:
            sheet_text = ", ".join(sheets) if sheets else "Default sheet"
            return f"{workbook_name} › {sheet_text}" if workbook_name else sheet_text

        def condition_description(condition: dict) -> str:
            method = str(condition.get("comparison_method", "")).replace("_", " ")
            if condition.get("comparison_method") == "numeric_tolerance":
                method = f"{method} ±{condition.get('numeric_tolerance') or 0}"
            return f"{condition.get('source_column')} ↔ {condition.get('destination_column')} ({method})"

        def tolerance_description(item: dict) -> str:
            from app.reconciliation_engine.tolerance import Band

            return Band(**{key: item.get(key) for key in ("field", "amount", "percent", "days", "similarity")}).describe()

        row = 2
        for number, rule in enumerate(rules, start=1):
            ws.merge_range(row, 0, row, last_column, f"RULE {number} — {', '.join(rule.get('source_sheets') or []) or 'Default sheet'} ↔ {', '.join(rule.get('destination_sheets') or []) or 'Default sheet'}", banner_format)
            ws.set_row(row, 22)
            row += 1
            failed = rule.get("status") != "completed"
            similarity_policy = rule.get("similarity_policy") or {}
            details = [
                ("Report label", rule.get("report_label") or ""),
                ("Source", source_description(rule.get("source_file"), rule.get("source_sheets") or [])),
                ("Destination", source_description(rule.get("destination_file"), rule.get("destination_sheets") or [])),
                ("Primary key", f"{' + '.join(rule.get('primary_key_source') or [])} → {' + '.join(rule.get('primary_key_destination') or [])}"),
                ("Secondary keys (must also match)", "; ".join(condition_description(item) for item in rule.get("secondary_conditions") or []) or "None"),
                ("Primary-key similarity", f"{similarity_policy.get('matcher_type_override') or 'automatic by data type'}; minimum confidence {f'{similarity_policy.get('threshold')}%' if similarity_policy.get('threshold') is not None else 'automatic'}"),
                ("Date-only override", "ENABLED — date-only keys can match unrelated same-day transactions" if rule.get("date_only_override") else "No"),
                ("Compared fields", rule.get("mapping_count", 0)),
                ("Prepared before matching", "; ".join(_transformation_text(item) for item in rule.get("transformations") or []) or "None"),
                ("Additional matching passes", "; ".join(pass_label(item, index) for index, item in enumerate(rule.get("matching_passes") or [], start=2)) or "None"),
                ("Tolerance (after matching)", "; ".join(tolerance_description(item) for item in rule.get("tolerances") or []) or "None"),
                ("Date format", "Month first (MM/DD/YYYY)" if rule.get("date_format") == "month_first" else "Day first (DD/MM/YYYY)"),
                ("Status", f"FAILED — {rule.get('error')}" if failed else "Completed"),
            ]
            for label, value in details:
                ws.write(row, 0, label, label_format)
                ws.merge_range(row, 1, row, last_column, value, warning_value_format if (label == "Status" and failed) or (label == "Date-only override" and rule.get("date_only_override")) else value_format)
                ws.set_row(row, 28 if len(str(value)) > 90 else 20)
                row += 1
            if not failed:
                summary = rule.get("summary") or {}
                for column, (label, _key) in enumerate(metrics):
                    ws.write(row, column, label, metric_header)
                ws.set_row(row, 32)
                for column, (_label, key) in enumerate(metrics):
                    ws.write_number(row + 1, column, int(summary.get(key, 0) or 0), metric_value)
                ws.set_row(row + 1, 22)
                row += 2
            row += 1
        ws.set_column(0, 0, 30)
        for column in range(1, len(metrics)):
            ws.set_column(column, column, 16)
        self.sheet_counts[sheet_name] = len(rules)

    @staticmethod
    def _csv_value(value: Any) -> Any:
        value = _cell_value(value)
        if isinstance(value, datetime):
            return value.isoformat(sep=" ")
        if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
            return "'" + value
        return value


def generate_enterprise_report(data: dict, config: dict, output_path: Path) -> ReportGeneration:
    report_config = ReportConfig(**config)
    return UniversalReporter(data, report_config, output_path).generate()
