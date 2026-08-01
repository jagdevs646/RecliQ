from pathlib import Path


EXCEL_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def is_supported_workbook(filename: str | None) -> bool:
    return Path(filename or "").suffix.lower() in {".xlsx", ".xls"}

