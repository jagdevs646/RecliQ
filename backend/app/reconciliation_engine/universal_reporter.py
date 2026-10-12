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


def _visible(column: str) -> bool:
    return column not in _HIDDEN_COLUMNS and not str(column).startswith(_HIDDEN_PREFIXES) and not str(column).upper().startswith("ROW")


def _cell_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
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
        self.formats = {
            "title": workbook.add_format({"bold": True, "font_size": 16, "font_color": "#FFFFFF", "bg_color": "#173B57", "valign": "vcenter"}),
            "subtitle": workbook.add_format({"font_color": "#526575", "text_wrap": True, "valign": "top"}),
            "header": workbook.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#177E78", "text_wrap": True, "valign": "vcenter"}),
            "note": workbook.add_format({"bold": True, "font_color": "#8A4B12", "bg_color": "#FFF2D9", "text_wrap": True}),
            "number": workbook.add_format({"num_format": self.config.number_format}),
            "percent": workbook.add_format({"num_format": "0.00%"}),
            "date": workbook.add_format({"num_format": self.config.date_format}),
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
        ws.set_column("A:A", 38)
        ws.set_column("B:B", 16)
        ws.set_column("C:C", 88)
        ws.set_column("D:D", 26)
        counts = self._counts()
        ws.merge_range("A1:D1", "Reconciliation Report", self.formats["title"])
        ws.merge_range("A2:D2", f"{self.file1} compared with {self.file2} · Generated {datetime.now().strftime('%d %b %Y %H:%M')}", self.formats["subtitle"])
        attention = counts["differ"] + counts["only_1"] + counts["only_2"]
        ws.merge_range("A4:D4", "All records match." if attention == 0 and counts["review"] == 0 else f"{attention:,} item(s) need attention. See the detail tabs.", self.formats["note"])
        ws.write_row("A6", ["Result", "Records", "What it means", "Tab"], self.formats["header"])
        results = [
            ("Matched – all fields agree", counts["matched"], "Found in both files; every compared field agrees.", self.sheet_names["matched"]),
            ("Matched – values differ", counts["differ"], "Found in both files, but one or more compared fields differ.", self.sheet_names["differences"]),
            (f"Only in {self.file1}", counts["only_1"], f"No matching record was found in {self.file2}.", self.sheet_names["only_1"]),
            (f"Only in {self.file2}", counts["only_2"], f"No matching record was found in {self.file1}.", self.sheet_names["only_2"]),
        ]
        if counts["review"]:
            results.append(("Needs review", counts["review"], "Possible or ambiguous matches to confirm.", self.sheet_names["review"]))
        if counts["resolved"]:
            results.append(("Auto-resolved by rules", counts["resolved"], "Exceptions explained by configured rules.", self.sheet_names["resolved"]))
        for row, values in enumerate(results, start=6):
            for col, value in enumerate(values):
                ws.write(row, col, value)
        ws.write(row + 2, 0, "Records in source")
        ws.write_number(row + 2, 1, counts["total_1"])
        ws.write(row + 3, 0, "Records in destination")
        ws.write_number(row + 3, 1, counts["total_2"])
        ws.write(row + 5, 0, "Overall status")
        ws.write(row + 5, 1, self.data.get("overall_status", ""))
        notes = self._matching_description()
        if notes:
            note_row = row + 8
            ws.write(note_row, 0, "How matching was done", self.formats["header"])
            for offset, (label, description) in enumerate(notes, start=1):
                ws.write(note_row + offset, 1, label, self.formats["header"])
                ws.write(note_row + offset, 2, description, self.formats["subtitle"])

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
            yield ("differences", self.sheet_names["differences"], "Differences", "Each row is one field that differs on a matched record.", headers, difference_rows, len(records), EXCEL_MAX_DATA_ROWS, "No differences were found.")

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

            yield (f"only_{file_number}", self.sheet_names[f"only_{file_number}"], f"Only in {this_file}", f"Records present in {this_file} without a matching record in {other_file}.", missing_headers, missing_rows, len(missing), EXCEL_MAX_DATA_ROWS, "Every record was matched.")

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

            yield ("review", self.sheet_names["review"], "Match Review", "Possible matches need a human decision before being treated as certain.", review_headers, review_rows, len(review), EXCEL_MAX_DATA_ROWS, "Nothing needs review.")

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

            yield ("matched", self.sheet_names["matched"], "Matched", "Records that agree on every compared field.", matched_headers, matched_rows, len(matched), MATCHED_WORKBOOK_ROW_CAP, "No record matched on every compared field.")

        if self.config.include_controls:
            checks = self._checks()
            yield ("checks", self.sheet_names["checks"], "Checks", "Control totals show whether all records are accounted for.", ["Check", self.file1, self.file2, "Result"], lambda: iter(checks), len(checks), EXCEL_MAX_DATA_ROWS, "No checks are available.")

        rules = self.data.get("sheet_rules") or []
        if rules and (len(rules) > 1 or any(rule.get("status") != "completed" or rule.get("secondary_conditions") or rule.get("transformations") or rule.get("matching_passes") or rule.get("tolerances") for rule in rules)):
            rule_headers = [
                "Rule", "Report label", "Source", "Destination", "Primary key", "Compared fields", "Transformations",
                "Matching passes", "Status", "Records in source", "Records in destination", "Exact matches",
                "Matches to confirm", "Of these, matched in later passes", "Several possible matches", "Not found",
                "Values differ", "Only source", "Only destination",
            ]
            def rule_rows():
                for index, rule in enumerate(rules, start=1):
                    transformations = []
                    for item in rule.get("transformations") or []:
                        detail = describe_transformation(item)
                        side = item.get("side", "both")
                        transformations.append(f"{detail} ({side})" if side != "both" else detail)
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

            yield ("resolved", self.sheet_names["resolved"], "Auto-resolved", "Exceptions explained by configured rules, with the rule and reason recorded.", resolved_headers, resolved_rows, len(resolved), EXCEL_MAX_DATA_ROWS, "No exceptions were auto-resolved.")

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
        ws = workbook.add_worksheet(sheet_name[:31])
        ws.hide_gridlines(2)
        ws.set_row(0, 26)
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
        sample = list(itertools.islice(rows_factory(), 100))
        for column, header in enumerate(headers):
            width = min(48, max(12, len(str(header)) + 2))
            for sample_row in sample:
                if column < len(sample_row) and sample_row[column] is not None:
                    width = min(60, max(width, min(60, len(str(sample_row[column])) + 2)))
            ws.set_column(column, column, width)
        percent_columns = {index for index, header in enumerate(headers) if str(header).strip().lower() in {"difference %", "match %"}}
        for row_index, values in enumerate(itertools.islice(rows_factory(), written_count), start=4):
            for column in range(len(headers)):
                value = _cell_value(values[column] if column < len(values) else None)
                if column in percent_columns and isinstance(value, (int, float)):
                    ws.write_number(row_index, column, value, self.formats["percent"])
                elif isinstance(value, bool):
                    ws.write_boolean(row_index, column, value)
                elif isinstance(value, (int, float)):
                    ws.write_number(row_index, column, value, self.formats["number"] if isinstance(value, float) and not value.is_integer() else None)
                elif isinstance(value, datetime):
                    ws.write_datetime(row_index, column, value, self.formats["date"])
                elif value is None:
                    ws.write_blank(row_index, column, None)
                else:
                    # XlsxWriter stores strings as literal cells; formulas and URLs are disabled.
                    ws.write_string(row_index, column, str(value))
        if total == 0:
            ws.write(4, 0, empty_message, self.formats["subtitle"])
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
