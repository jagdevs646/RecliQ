"""Compact, printable reconciliation exception reports.

This exporter deliberately reads only the exception categories.  It is kept
separate from the full Excel report so a reviewer can receive a focused PDF
without materialising matched-record data or changing reconciliation output.
"""
from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Iterable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle


# Missing-record tables are intentionally compact: their purpose is to tell a
# reviewer which records are absent, not to recreate the source workbook.
_MAX_MISSING_DETAIL_COLUMNS = 4
_MAX_CELL_CHARACTERS = 500
_HIDDEN_SOURCE_COLUMNS = {
    "File Pair ID",
    "Sheet Rule ID",
    "Sheet Pair",
    "MATCH TYPE",
    "MATCH CONFIDENCE",
    "MATCH STATUS",
    "MATCH THRESHOLD",
    "IDENTITY CLASSIFICATION",
    "MATCH EXPLANATION",
    "GROUPED ROWS",
    "COMPOSITE MATCH KEY",
    "MATCHED COMPOSITE KEY",
    "MATCH KEY",
    "MATCHED KEY",
    "SECONDARY KEY",
    "MATCHED SECONDARY KEY",
    "GROUP CLASSIFICATION",
    "CANDIDATE KEY",
    "MATCH PASS",
    "NORMALIZATION APPLIED",
    "MATCH METHOD",
    "REVIEW HISTORY",
    "LEARNED CONFIDENCE",
    "AUTO-RESOLVED BY",
}
_HIDDEN_SOURCE_PREFIXES = ("NORM_", "MATCHED NORM_", "__")


@dataclass(frozen=True)
class ExceptionPdfGeneration:
    """Useful generation facts for the download route and focused tests."""

    output_path: Path
    section_totals: dict[str, int]
    displayed_rows: dict[str, int]


@dataclass(frozen=True)
class _SectionRows:
    total: int
    rows: list[dict[str, Any]]


def _visible_source_column(column: str) -> bool:
    upper = str(column).upper()
    return (
        column not in _HIDDEN_SOURCE_COLUMNS
        and not str(column).startswith(_HIDDEN_SOURCE_PREFIXES)
        and not upper.startswith("ROW")
    )


def _section_rows(records: Iterable[dict[str, Any]] | None, limit: int) -> _SectionRows:
    records = records if records is not None else []
    try:
        total = len(records)  # type: ignore[arg-type]
    except TypeError:
        sampled = list(itertools.islice(records, limit + 1))
        return _SectionRows(total=len(sampled), rows=sampled[:limit])
    return _SectionRows(total=int(total), rows=list(itertools.islice(records, limit)))


def _file_label(value: Any, fallback: str) -> str:
    name = Path(str(value or fallback)).name.strip()
    return name or fallback


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return f"{value:,}" if value.is_integer() else f"{value:,.6f}".rstrip("0").rstrip(".")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ", "))
    text = str(value)
    if text.lower() in {"nan", "nat", "none"}:
        return ""
    return text


def _display_text(value: Any) -> str:
    text = _cell_text(value).replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > _MAX_CELL_CHARACTERS:
        return f"{text[:_MAX_CELL_CHARACTERS - 1]}..."
    return text


def _percentage(value: Any) -> str:
    if value is None or value == "":
        return "--"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    if not math.isfinite(number):
        return "--"
    return f"{number * 100:,.2f}%"


def _record_value(record: dict[str, Any], column: str) -> Any:
    """Read a source field even when a workbook changes its header casing."""
    if column in record:
        return record[column]
    normalized = str(column).casefold()
    for record_column, value in record.items():
        if str(record_column).casefold() == normalized:
            return value
    return None


def _record_key(record: dict[str, Any], key_columns: list[str]) -> str:
    for key in ("Primary Key", "MATCH KEY", "COMPOSITE MATCH KEY"):
        value = _record_value(record, key)
        if value not in (None, ""):
            return _cell_text(value)
    values = [
        _cell_text(value)
        for key in key_columns
        if (value := _record_value(record, key)) not in (None, "")
    ]
    if values:
        return " | ".join(values)
    for column, value in record.items():
        if "key" in str(column).casefold() and value not in (None, ""):
            return _cell_text(value)
    return "--"


def _key_columns(data: dict[str, Any]) -> list[str]:
    metadata = data.get("metadata") or {}
    candidates: list[Any] = list(metadata.get("matching_keys") or [])
    for rule in data.get("sheet_rules") or []:
        for name in ("primary_key_source", "primary_key_destination"):
            value = rule.get(name)
            candidates.extend(value if isinstance(value, list) else [value])
    columns: dict[str, str] = {}
    for value in candidates:
        name = str(value or "").strip()
        if name:
            columns.setdefault(name.casefold(), name)
    return list(columns.values())


def _source_columns(rows: list[dict[str, Any]], key_columns: list[str]) -> list[str]:
    key_column_names = {column.casefold() for column in key_columns}
    columns: dict[str, None] = {}
    for record in rows:
        for column in record:
            name = str(column)
            if _visible_source_column(name) and name.casefold() not in key_column_names:
                columns.setdefault(name, None)
    return list(columns)


def _paragraph(value: Any, style: ParagraphStyle) -> Paragraph:
    text = escape(_display_text(value), quote=False).replace("\n", "<br/>")
    return Paragraph(text or "&nbsp;", style)


def _table(
    headers: list[str],
    rows: list[list[Any]],
    widths: list[float],
    header_style: ParagraphStyle,
    body_style: ParagraphStyle,
    empty_text: str,
) -> LongTable:
    content: list[list[Any]] = [[_paragraph(header, header_style) for header in headers]]
    if rows:
        content.extend([[_paragraph(value, body_style) for value in row] for row in rows])
    else:
        content.append([_paragraph(empty_text, body_style), *[_paragraph("", body_style) for _ in headers[1:]]])
    table = LongTable(content, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#133B45")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C7D7DB")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F8F8")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _section_note(total: int, displayed: int, description: str) -> str:
    if total == 0:
        return description
    if displayed < total:
        return f"{description} Showing the first {displayed:,} of {total:,} records. The complete data remains available in the Excel report."
    return f"{description} {total:,} record{'s' if total != 1 else ''}."


def _exception_note(total: int, displayed: int) -> str:
    description = "Each row identifies one field whose matched values differ."
    if total == 0:
        return description
    if displayed < total:
        return f"{description} Showing the first {displayed:,} of {total:,} field differences. The complete data remains available in the Excel report."
    return f"{description} {total:,} field difference{'s' if total != 1 else ''}."


def _missing_widths(column_count: int, usable_width: float) -> list[float]:
    key_width = min(max(96, usable_width * 0.20), 155)
    remaining = usable_width - key_width
    return [key_width, *([remaining / max(1, column_count - 1)] * (column_count - 1))]


def _append_missing_section(
    story: list[Any],
    *,
    title: str,
    file_label: str,
    section: _SectionRows,
    key_columns: list[str],
    usable_width: float,
    heading_style: ParagraphStyle,
    note_style: ParagraphStyle,
    header_style: ParagraphStyle,
    body_style: ParagraphStyle,
) -> None:
    columns = _source_columns(section.rows, key_columns)[:_MAX_MISSING_DETAIL_COLUMNS]
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(escape(title, quote=False), heading_style))
    story.append(Paragraph(
        _section_note(section.total, len(section.rows), f"Records found only in {escape(file_label, quote=False)}."),
        note_style,
    ))
    if not columns:
        story.append(_table(["Key"], [], [usable_width], header_style, body_style, "No records were found."))
        return
    headers = ["Key", *columns]
    values = [
        [_record_key(record, key_columns), *[record.get(column) for column in columns]]
        for record in section.rows
    ]
    story.append(_table(headers, values, _missing_widths(len(headers), usable_width), header_style, body_style, "No records were found."))


def generate_exception_pdf_report(
    data: dict[str, Any],
    output_path: Path,
    *,
    max_rows_per_section: int = 10_000,
) -> ExceptionPdfGeneration:
    """Create a printable three-section PDF from stored reconciliation data.

    Only the three exception categories are sampled. This makes on-demand
    downloads bounded even when a reconciliation's full result is very large.
    """
    if max_rows_per_section < 1:
        raise ValueError("max_rows_per_section must be at least 1")

    metadata = data.get("metadata") or {}
    file_1 = _file_label(metadata.get("file_1_name"), "File 1")
    file_2 = _file_label(metadata.get("file_2_name"), "File 2")
    key_columns = _key_columns(data)
    exceptions = _section_rows(data.get("exceptions"), max_rows_per_section)
    only_file_1 = _section_rows(data.get("missing_in_file_2"), max_rows_per_section)
    only_file_2 = _section_rows(data.get("missing_in_file_1"), max_rows_per_section)

    page_width, _page_height = landscape(A4)
    margin = 10 * mm
    usable_width = page_width - (margin * 2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(A4),
        leftMargin=margin,
        rightMargin=margin,
        topMargin=11 * mm,
        bottomMargin=10 * mm,
        title="RecliQ exception report",
        author="RecliQ",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ExceptionPdfTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=20,
        leading=24, textColor=colors.HexColor("#12343B"), spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        "ExceptionPdfSubtitle", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.5,
        leading=13, textColor=colors.HexColor("#4D6268"), spaceAfter=4,
    )
    heading_style = ParagraphStyle(
        "ExceptionPdfHeading", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12,
        leading=15, textColor=colors.HexColor("#12343B"), spaceAfter=2,
    )
    note_style = ParagraphStyle(
        "ExceptionPdfNote", parent=styles["BodyText"], fontName="Helvetica", fontSize=8.2,
        leading=10.5, textColor=colors.HexColor("#50666B"), spaceAfter=3,
    )
    header_style = ParagraphStyle(
        "ExceptionPdfHeader", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=7.6,
        leading=9.2, textColor=colors.white,
    )
    body_style = ParagraphStyle(
        "ExceptionPdfBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.2,
        leading=9.1, textColor=colors.HexColor("#1F3034"),
    )

    generated_at = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    story: list[Any] = [
        Paragraph("RecliQ", subtitle_style),
        Paragraph("Exception review report", title_style),
        Paragraph(
            f"{escape(file_1, quote=False)} compared with {escape(file_2, quote=False)} | Generated {generated_at}",
            subtitle_style,
        ),
    ]

    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Field exceptions", heading_style))
    story.append(Paragraph(_exception_note(exceptions.total, len(exceptions.rows)), note_style))
    exception_headers = ["Key", "Field", f"{file_1} value", f"{file_2} value", "Difference %"]
    exception_values = [
        [
            _record_key(record, key_columns),
            record.get("Field"),
            record.get("File 1 Value"),
            record.get("File 2 Value"),
            _percentage(record.get("Difference %")),
        ]
        for record in exceptions.rows
    ]
    exception_widths = [
        usable_width * 0.20,
        usable_width * 0.14,
        usable_width * 0.25,
        usable_width * 0.25,
        usable_width * 0.16,
    ]
    story.append(_table(
        exception_headers,
        exception_values,
        exception_widths,
        header_style,
        body_style,
        "No field exceptions were found.",
    ))

    _append_missing_section(
        story,
        title=f"Only in {file_1}",
        file_label=file_1,
        section=only_file_1,
        key_columns=key_columns,
        usable_width=usable_width,
        heading_style=heading_style,
        note_style=note_style,
        header_style=header_style,
        body_style=body_style,
    )
    _append_missing_section(
        story,
        title=f"Only in {file_2}",
        file_label=file_2,
        section=only_file_2,
        key_columns=key_columns,
        usable_width=usable_width,
        heading_style=heading_style,
        note_style=note_style,
        header_style=header_style,
        body_style=body_style,
    )
    document.build(story)
    return ExceptionPdfGeneration(
        output_path=output_path,
        section_totals={
            "exceptions": exceptions.total,
            "only_in_file_1": only_file_1.total,
            "only_in_file_2": only_file_2.total,
        },
        displayed_rows={
            "exceptions": len(exceptions.rows),
            "only_in_file_1": len(only_file_1.rows),
            "only_in_file_2": len(only_file_2.rows),
        },
    )
