from pathlib import Path


EXCEL_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SUPPORTED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".tsv", ".txt", ".pdf", ".docx", ".doc", ".md"}


def is_supported_workbook(filename: str | None) -> bool:
    return Path(filename or "").suffix.lower() in SUPPORTED_EXTENSIONS
