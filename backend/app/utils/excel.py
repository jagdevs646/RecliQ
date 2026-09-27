from pathlib import Path

from app.core.upload_validation import SUPPORTED_FORMATS


EXCEL_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SUPPORTED_EXTENSIONS = set(SUPPORTED_FORMATS)


def is_supported_workbook(filename: str | None) -> bool:
    return Path(filename or "").suffix.lower() in SUPPORTED_EXTENSIONS
