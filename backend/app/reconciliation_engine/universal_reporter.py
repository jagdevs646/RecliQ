from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from openpyxl.chart import BarChart, DoughnutChart, Reference
from openpyxl.chart.series import DataPoint
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

def excel_writer_engine() -> str:
    # We must use openpyxl because our enterprise styling relies on openpyxl classes
    return "openpyxl"

class ReportConfig:
    def __init__(self,
                 include_summary: bool = True,
                 include_exceptions: bool = True,
                 include_matched: bool = True,
                 include_missing_file_1: bool = True,
                 include_missing_file_2: bool = True,
                 include_field_differences: bool = True,
                 include_controls: bool = True,
                 date_format: str = "YYYY-MM-DD",
                 number_format: str = "#,##0.00"):
        self.include_summary = include_summary
        self.include_exceptions = include_exceptions
        self.include_matched = include_matched
        self.include_missing_file_1 = include_missing_file_1
        self.include_missing_file_2 = include_missing_file_2
        self.include_field_differences = include_field_differences
        self.include_controls = include_controls
        self.date_format = date_format
        self.number_format = number_format

class UniversalReporter:
    def __init__(self, data: dict, config: ReportConfig, output_path: Path):
        self.data = data
        self.config = config
        self.output_path = output_path
        self.engine = excel_writer_engine()
        self.colors = {
            # Brand
            "primary":     "2C3E50",   # dark navy
            "accent":      "087D72",   # RecliQ teal
            "header_fg":   "FFFFFF",
            # Semantic
            "pass":        "1E8449",   # rich green
            "warning":     "D4AC0D",   # amber
            "exception":   "CA6F1E",   # orange
            "critical":    "C0392B",   # red
            "info":        "1A5276",   # dark blue
            # Backgrounds
            "pass_bg":     "D5F5E3",
            "warning_bg":  "FDEBD0",
            "exception_bg":"FADBD8",
            "neutral_bg":  "F2F3F4",
            "card_bg":     "EBF5FB",
        }
    
    def _style_header(self, worksheet, columns: list[str], theme_color: str | None = None) -> None:
        bg = theme_color if theme_color else self.colors["primary"]
        fill = PatternFill("solid", fgColor=bg)
        font = Font(bold=True, color=self.colors["header_fg"], size=11)
        thin = Side(style="thin", color="BDC3C7")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        for col_num, col_name in enumerate(columns, start=1):
            cell = worksheet.cell(row=1, column=col_num, value=col_name)
            cell.font = font
            cell.fill = fill
            cell.border = border
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        worksheet.row_dimensions[1].height = 28

    def _auto_fit_columns(self, worksheet, df: pd.DataFrame) -> None:
        for i, col in enumerate(df.columns, start=1):
            col_series = df[col].head(500).fillna("").astype(str)
            max_len = max(int(col_series.str.len().max() if not col_series.empty else 0), len(str(col))) + 2
            width = min(max_len, 60)
            worksheet.column_dimensions[get_column_letter(i)].width = width

    def _format_worksheet(
        self, worksheet, df: pd.DataFrame, theme_color: str | None = None
    ) -> None:
        self._style_header(worksheet, df.columns.tolist(), theme_color)
        self._auto_fit_columns(worksheet, df)
        worksheet.freeze_panes = "A2"
        if not df.empty:
            worksheet.auto_filter.ref = worksheet.dimensions

        # Alternating row fills + number formatting
        alt_fill = PatternFill("solid", fgColor="F8F9FA")
        for row_idx, row in enumerate(df.itertuples(index=False), start=2):
            if row_idx % 2 == 0:
                for col_idx in range(1, len(df.columns) + 1):
                    worksheet.cell(row=row_idx, column=col_idx).fill = alt_fill
            for col_idx, (col_name, value) in enumerate(
                zip(df.columns, row), start=1
            ):
                cell = worksheet.cell(row=row_idx, column=col_idx)
                if isinstance(value, (int, float)):
                    # Apply percentage format for columns whose name ends with %
                    if "%" in str(col_name):
                        cell.number_format = "0.0%"
                    else:
                        cell.number_format = self.config.number_format

    def generate(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with pd.ExcelWriter(self.output_path, engine=self.engine) as writer:
            if self.config.include_summary:
                self._generate_summary(writer)
            if self.config.include_exceptions:
                self._generate_exceptions(writer)
            if self.config.include_matched:
                self._generate_matched(writer)
            if self.config.include_missing_file_1:
                self._generate_missing(writer, 1)
            if self.config.include_missing_file_2:
                self._generate_missing(writer, 2)
            if self.config.include_field_differences:
                self._generate_field_differences(writer)
            if self.config.include_controls:
                self._generate_controls(writer)
                
            # If nothing was generated (all false), generate an empty sheet
            if len(writer.sheets) == 0:
                pd.DataFrame([{"Message": "No sections selected"}]).to_excel(writer, sheet_name="Empty", index=False)

    def _thin_border(self, color: str = "BDC3C7") -> Border:
        s = Side(style="thin", color=color)
        return Border(left=s, right=s, top=s, bottom=s)

    def _write_section_title(self, ws, row: int, col: int, text: str) -> None:
        cell = ws.cell(row=row, column=col, value=text)
        cell.font = Font(size=13, bold=True, color=self.colors["primary"])
        ws.row_dimensions[row].height = 22

    def _generate_summary(self, writer: pd.ExcelWriter) -> None:  # noqa: C901
        workbook = writer.book
        worksheet = workbook.create_sheet("01 Summary Dashboard")
        if worksheet in workbook.worksheets:
            workbook.move_sheet(worksheet, offset=-len(workbook.worksheets))

        worksheet.sheet_view.showGridLines = False

        # ── Palette shortcuts ──────────────────────────────────────────────
        primary   = self.colors["primary"]
        accent    = self.colors["accent"]
        pass_c    = self.colors["pass"]
        exc_c     = self.colors["exception"]
        warn_c    = self.colors["warning"]
        crit_c    = self.colors["critical"]
        neutral   = self.colors["neutral_bg"]
        card_bg   = self.colors["card_bg"]
        white     = "FFFFFF"

        border = self._thin_border()

        # ── Collect data ───────────────────────────────────────────────────
        meta    = self.data.get("metadata", {})
        stats   = self.data.get("statistics", {})
        status  = self.data.get("overall_status", "UNKNOWN")
        field_exc  = self.data.get("field_exception_summary", [])
        exc_summary = self.data.get("exception_summary", [])

        total_f1  = stats.get("total_file_1", 0)
        total_f2  = stats.get("total_file_2", 0)
        total_rec = max(total_f1, total_f2)
        matched   = stats.get("matched", 0)
        mismatched = stats.get("mismatched", 0)
        missing1  = stats.get("missing_in_file_1", 0)
        missing2  = stats.get("missing_in_file_2", 0)
        total_missing = missing1 + missing2
        total_exc = mismatched + total_missing

        # Decimal percentages (correct: 0.90, NOT 90)
        match_rate  = (matched   / total_rec) if total_rec > 0 else 0.0
        exc_rate    = (total_exc / total_rec) if total_rec > 0 else 0.0

        file1_name = meta.get("file_1_name", "Source 1")
        file2_name = meta.get("file_2_name", "Source 2")

        # ═══════════════════════════════════════════════════════════════════
        # SECTION 1 – Title banner (rows 1-3)
        # ═══════════════════════════════════════════════════════════════════
        banner = worksheet.cell(row=1, column=2, value="  RECLIQ — RECONCILIATION EXECUTIVE DASHBOARD")
        banner.font = Font(size=18, bold=True, color=white)
        banner.fill = PatternFill("solid", fgColor=primary)
        banner.alignment = Alignment(vertical="center")
        worksheet.merge_cells(start_row=1, start_column=2, end_row=1, end_column=12)
        worksheet.row_dimensions[1].height = 36

        sub = worksheet.cell(
            row=2, column=2,
            value=f"  Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC  |  "
                  f"{file1_name} vs {file2_name}  |  Matching key: {', '.join(meta.get('matching_keys', ['—']))}"
        )
        sub.font = Font(size=10, italic=True, color="5D6D7E")
        worksheet.merge_cells(start_row=2, start_column=2, end_row=2, end_column=12)
        worksheet.row_dimensions[2].height = 18

        # ═══════════════════════════════════════════════════════════════════
        # SECTION 2 – KPI Cards (rows 4-7)
        # ═══════════════════════════════════════════════════════════════════
        self._write_section_title(worksheet, 4, 2, "KEY METRICS")

        # We place numeric anchors in row 6 (hidden-ish), then formula cards in row 5-7
        # Anchors – actual integer values that formulas reference:
        ANCHOR_ROW     = 6   # invisible anchor row with raw values
        CARD_LABEL_ROW = 7
        CARD_VAL_ROW   = 8

        kpi_cols = {
            # col : (label, anchor_value, fmt)
            2: ("STATUS",           status,          None),
            3: ("RECORDS COMPARED", total_rec,       "#,##0"),
            4: ("MATCHED",          matched,          "#,##0"),
            5: ("EXCEPTIONS",       total_exc,        "#,##0"),
            6: ("MISSING",          total_missing,    "#,##0"),
            7: ("MATCH RATE",       None,             "0.0%"),   # formula
            8: ("EXCEPTION RATE",   None,             "0.0%"),   # formula
        }

        col_letters = {c: get_column_letter(c) for c in kpi_cols}

        for col, (label, anchor_val, fmt) in kpi_cols.items():
            ltr = col_letters[col]

            # Write anchor (raw numeric value) – row 6
            if anchor_val is not None and not isinstance(anchor_val, str):
                anchor_cell = worksheet.cell(row=ANCHOR_ROW, column=col, value=anchor_val)
                anchor_cell.font = Font(size=9, color="AAAAAA")

            # Card background – label row
            lbl_cell = worksheet.cell(row=CARD_LABEL_ROW, column=col, value=label)
            lbl_cell.font = Font(size=9, bold=True, color="7F8C8D")
            lbl_cell.fill = PatternFill("solid", fgColor=neutral)
            lbl_cell.alignment = Alignment(horizontal="center", vertical="center")
            lbl_cell.border = border
            worksheet.row_dimensions[CARD_LABEL_ROW].height = 18

            # Card value row
            val_cell = worksheet.cell(row=CARD_VAL_ROW, column=col)
            val_cell.alignment = Alignment(horizontal="center", vertical="center")
            val_cell.border = border
            worksheet.row_dimensions[CARD_VAL_ROW].height = 34

            if label == "MATCH RATE":
                # Formula: =IF(C6>0, D6/C6, 0)  — matched/total_rec
                val_cell.value = f"=IF({col_letters[3]}{ANCHOR_ROW}>0,{col_letters[4]}{ANCHOR_ROW}/{col_letters[3]}{ANCHOR_ROW},0)"
                val_cell.number_format = "0.0%"
                val_cell.font = Font(size=18, bold=True, color=pass_c if match_rate >= 0.95 else exc_c)
                val_cell.fill = PatternFill("solid", fgColor=self.colors["pass_bg"] if match_rate >= 0.95 else self.colors["warning_bg"])

            elif label == "EXCEPTION RATE":
                # Formula: =IF(C6>0, E6/C6, 0)  — exceptions/total_rec
                val_cell.value = f"=IF({col_letters[3]}{ANCHOR_ROW}>0,{col_letters[5]}{ANCHOR_ROW}/{col_letters[3]}{ANCHOR_ROW},0)"
                val_cell.number_format = "0.0%"
                val_cell.font = Font(size=18, bold=True, color=exc_c if exc_rate > 0 else pass_c)
                val_cell.fill = PatternFill("solid", fgColor=self.colors["exception_bg"] if exc_rate > 0 else self.colors["pass_bg"])

            elif label == "STATUS":
                val_cell.value = status
                val_cell.font = Font(size=13, bold=True, color=white)
                if "CRITICAL" in status.upper() or "FAIL" in status.upper():
                    val_cell.fill = PatternFill("solid", fgColor=crit_c)
                elif "EXCEPTION" in status.upper():
                    val_cell.fill = PatternFill("solid", fgColor=exc_c)
                elif "WARNING" in status.upper():
                    val_cell.fill = PatternFill("solid", fgColor=warn_c)
                else:  # PASSED
                    val_cell.fill = PatternFill("solid", fgColor=pass_c)

            else:
                val_cell.value = anchor_val
                val_cell.number_format = fmt or "#,##0"
                val_cell.font = Font(size=18, bold=True, color=primary)
                val_cell.fill = PatternFill("solid", fgColor=card_bg)

            worksheet.column_dimensions[ltr].width = 18

        # ═══════════════════════════════════════════════════════════════════
        # SECTION 3 – Charts data tables (columns Z onward, rows 1+)
        # ═══════════════════════════════════════════════════════════════════
        DATA_COL = 27   # column AA – far right, not normally visible
        DR = 2          # data start row

        # Outcome donut data
        outcome_cats   = ["Matched", "Discrepancies", f"Missing {file1_name}", f"Missing {file2_name}"]
        outcome_values = [matched, mismatched, missing1, missing2]
        worksheet.cell(row=DR,   column=DATA_COL, value="Outcome Category")
        worksheet.cell(row=DR,   column=DATA_COL+1, value="Count")
        for i, (cat, val) in enumerate(zip(outcome_cats, outcome_values), start=1):
            worksheet.cell(row=DR+i, column=DATA_COL,   value=cat)
            worksheet.cell(row=DR+i, column=DATA_COL+1, value=val)

        # ── Outcome Donut Chart ────────────────────────────────────────────
        if total_rec > 0:
            self._write_section_title(worksheet, 10, 2, "RECONCILIATION OUTCOME")

            donut = DoughnutChart()
            donut.title = None
            donut.style = 10
            donut.holeSize = 55
            labels_ref = Reference(worksheet, min_col=DATA_COL, min_row=DR+1, max_row=DR+4)
            data_ref   = Reference(worksheet, min_col=DATA_COL+1, min_row=DR, max_row=DR+4)
            donut.add_data(data_ref, titles_from_data=True)
            donut.set_categories(labels_ref)
            donut.width  = 14
            donut.height = 12
            worksheet.add_chart(donut, "B11")

        # ── Field-level Bar Chart ──────────────────────────────────────────
        FIELD_DR = DR + 7
        if field_exc:
            self._write_section_title(worksheet, 10, 7, "FIELD MISMATCH ANALYSIS")

            worksheet.cell(row=FIELD_DR, column=DATA_COL+3, value="Field")
            worksheet.cell(row=FIELD_DR, column=DATA_COL+4, value="Mismatches")
            top_fields = sorted(field_exc, key=lambda x: x.get("Mismatch", 0), reverse=True)[:10]
            for j, fe in enumerate(top_fields, start=1):
                worksheet.cell(row=FIELD_DR+j, column=DATA_COL+3, value=fe.get("Field", ""))
                worksheet.cell(row=FIELD_DR+j, column=DATA_COL+4, value=fe.get("Mismatch", 0))

            bar = BarChart()
            bar.type   = "bar"  # horizontal
            bar.style  = 10
            bar.title  = None
            bar.y_axis.title = None
            bar.x_axis.title = "Exception Count"
            bar_cats = Reference(worksheet, min_col=DATA_COL+3, min_row=FIELD_DR+1, max_row=FIELD_DR+len(top_fields))
            bar_data = Reference(worksheet, min_col=DATA_COL+4, min_row=FIELD_DR, max_row=FIELD_DR+len(top_fields))
            bar.add_data(bar_data, titles_from_data=True)
            bar.set_categories(bar_cats)
            bar.width  = 18
            bar.height = 12
            worksheet.add_chart(bar, "G11")

        # ═══════════════════════════════════════════════════════════════════
        # SECTION 4 – Exception Summary Table (row 25+)
        # ═══════════════════════════════════════════════════════════════════
        exc_tbl_row = 25
        self._write_section_title(worksheet, exc_tbl_row, 2, "EXCEPTION BREAKDOWN")
        exc_tbl_row += 1

        if exc_summary:
            hdr_fill = PatternFill("solid", fgColor=primary)
            hdr_font = Font(bold=True, color=white, size=10)
            for ci, hdr in enumerate(["Exception Type", "Count", "Severity / Impact"], start=2):
                c = worksheet.cell(row=exc_tbl_row, column=ci, value=hdr)
                c.font = hdr_font; c.fill = hdr_fill; c.border = border
                c.alignment = Alignment(horizontal="center", vertical="center")
            exc_tbl_row += 1
            for exc in exc_summary:
                worksheet.cell(row=exc_tbl_row, column=2, value=exc.get("Exception Type", exc.get("Type", ""))).border = border
                worksheet.cell(row=exc_tbl_row, column=3, value=exc.get("Count", 0)).border = border
                worksheet.cell(row=exc_tbl_row, column=4, value=exc.get("Impact", "")).border = border
                exc_tbl_row += 1
        else:
            ok_cell = worksheet.cell(row=exc_tbl_row, column=2, value="✔  No exceptions found — reconciliation passed cleanly.")
            ok_cell.font = Font(color=pass_c, bold=True)
            exc_tbl_row += 1

        # ═══════════════════════════════════════════════════════════════════
        # SECTION 5 – Field-level summary table
        # ═══════════════════════════════════════════════════════════════════
        fld_tbl_row = exc_tbl_row + 2
        self._write_section_title(worksheet, fld_tbl_row, 2, "FIELD-LEVEL PERFORMANCE")
        fld_tbl_row += 1

        if field_exc:
            hdr_fill = PatternFill("solid", fgColor=accent)
            hdr_font = Font(bold=True, color=white, size=10)
            for ci, hdr in enumerate(["Field", "Matched", "Mismatches", "Match Rate"], start=2):
                c = worksheet.cell(row=fld_tbl_row, column=ci, value=hdr)
                c.font = hdr_font; c.fill = hdr_fill; c.border = border
                c.alignment = Alignment(horizontal="center", vertical="center")
            fld_tbl_row += 1

            for f_exc in field_exc:
                fld_matched  = f_exc.get("Matched", 0)
                fld_mismatch = f_exc.get("Mismatch", 0)
                fld_total    = fld_matched + fld_mismatch
                # Write raw matched/mismatch counts first
                worksheet.cell(row=fld_tbl_row, column=2, value=f_exc.get("Field", "")).border = border
                worksheet.cell(row=fld_tbl_row, column=3, value=fld_matched).border = border
                worksheet.cell(row=fld_tbl_row, column=4, value=fld_mismatch).border = border

                # Match Rate cell with Excel formula: =IF(C+D>0, C/(C+D), 0)
                c_ltr = get_column_letter(3)
                d_ltr = get_column_letter(4)
                rate_cell = worksheet.cell(
                    row=fld_tbl_row, column=5,
                    value=f"=IF({c_ltr}{fld_tbl_row}+{d_ltr}{fld_tbl_row}>0,{c_ltr}{fld_tbl_row}/({c_ltr}{fld_tbl_row}+{d_ltr}{fld_tbl_row}),0)"
                )
                rate_cell.number_format = "0.0%"
                rate_cell.border = border
                # Colour-code poor match rates
                raw_rate = (fld_matched / fld_total) if fld_total > 0 else 0.0
                if raw_rate < 0.8:
                    rate_cell.fill = PatternFill("solid", fgColor=self.colors["exception_bg"])
                elif raw_rate < 0.95:
                    rate_cell.fill = PatternFill("solid", fgColor=self.colors["warning_bg"])
                else:
                    rate_cell.fill = PatternFill("solid", fgColor=self.colors["pass_bg"])
                fld_tbl_row += 1
        else:
            worksheet.cell(row=fld_tbl_row, column=2, value="No field-level mismatches.").font = Font(color=pass_c)
            fld_tbl_row += 1

        # ═══════════════════════════════════════════════════════════════════
        # SECTION 6 – Key Insights
        # ═══════════════════════════════════════════════════════════════════
        ins_row = fld_tbl_row + 2
        self._write_section_title(worksheet, ins_row, 2, "KEY INSIGHTS")
        ins_row += 1

        insights: list[str] = []
        if total_rec > 0:
            insights.append(f"• {match_rate*100:.1f}% of records matched perfectly ({matched:,} of {total_rec:,}).")
        if mismatched > 0:
            insights.append(f"• {mismatched:,} discrepancies identified requiring review.")
        if total_missing > 0:
            insights.append(f"• {total_missing:,} records are missing from one or both sources.")
        if field_exc:
            top_f = sorted(field_exc, key=lambda x: x.get("Mismatch", 0), reverse=True)[0]
            if top_f.get("Mismatch", 0) > 0:
                insights.append(f"• Field '{top_f['Field']}' has the highest exception count ({top_f['Mismatch']:,}).")
        if not insights:
            insights.append("• All records reconciled. No issues found.")

        for insight in insights:
            ic = worksheet.cell(row=ins_row, column=2, value=insight)
            ic.font = Font(size=11, color=primary)
            ins_row += 1

        # ═══════════════════════════════════════════════════════════════════
        # Column widths for the summary sheet
        # ═══════════════════════════════════════════════════════════════════
        for col in range(2, 13):
            ltr = get_column_letter(col)
            if worksheet.column_dimensions[ltr].width < 18:
                worksheet.column_dimensions[ltr].width = 18

    def _generate_exceptions(self, writer: pd.ExcelWriter) -> None:
        df = pd.DataFrame(self.data.get("exceptions", []))
        sheet_name = "02 Exceptions"
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        ws = writer.sheets[sheet_name]
        self._format_worksheet(ws, df, theme_color=self.colors["exception"])
        ws.sheet_properties.tabColor = self.colors["exception"]

    def _generate_matched(self, writer: pd.ExcelWriter) -> None:
        df = pd.DataFrame(self.data.get("matched_records", []))
        sheet_name = "03 Matched Records"
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        ws = writer.sheets[sheet_name]
        self._format_worksheet(ws, df, theme_color=self.colors["pass"])
        ws.sheet_properties.tabColor = self.colors["pass"]

    def _generate_missing(self, writer: pd.ExcelWriter, file_num: int) -> None:
        key = "missing_in_file_1" if file_num == 1 else "missing_in_file_2"
        df = pd.DataFrame(self.data.get(key, []))
        meta = self.data.get("metadata", {})
        fname = meta.get(f"file_{file_num}_name", f"Source {file_num}")
        sheet_num = "04" if file_num == 1 else "05"
        sheet_name = f"{sheet_num} Missing — {fname}"[:31]
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        ws = writer.sheets[sheet_name]
        self._format_worksheet(ws, df, theme_color=self.colors["warning"])
        ws.sheet_properties.tabColor = self.colors["warning"]

    def _generate_field_differences(self, writer: pd.ExcelWriter) -> None:
        df = pd.DataFrame(self.data.get("field_differences", []))
        sheet_name = "06 Field Differences"
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        ws = writer.sheets[sheet_name]
        self._format_worksheet(ws, df, theme_color=self.colors["info"])
        ws.sheet_properties.tabColor = self.colors["info"]
        # Ensure any % columns are formatted correctly as decimals -> percentage
        for col_idx, col_name in enumerate(df.columns, start=1):
            if "%" in str(col_name):
                for row_idx in range(2, len(df) + 2):
                    ws.cell(row=row_idx, column=col_idx).number_format = "0.0%"

    def _generate_controls(self, writer: pd.ExcelWriter) -> None:
        df = pd.DataFrame(self.data.get("control_checks", []))
        sheet_name = "07 Control Checks"
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        ws = writer.sheets[sheet_name]
        self._format_worksheet(ws, df, theme_color=self.colors["accent"])
        ws.sheet_properties.tabColor = self.colors["accent"]


def generate_enterprise_report(data: dict, config: dict, output_path: Path) -> None:
    report_config = ReportConfig(**config)
    reporter = UniversalReporter(data, report_config, output_path)
    reporter.generate()
