"""Validate uploads by content before anything is stored.

The file name only says which parser the user expects. The bytes must agree:
an ``.xlsx`` must be an Office Open XML spreadsheet, an ``.xls`` an OLE2
compound file, a ``.pdf`` must start with ``%PDF-`` and delimited text must not
be binary. Size and row limits are enforced here, before parsing.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from typing import BinaryIO

ZIP_SIGNATURE = b"PK\x03\x04"
OLE2_SIGNATURE = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
PDF_SIGNATURE = b"%PDF-"
_BINARY_SIGNATURES = (ZIP_SIGNATURE, OLE2_SIGNATURE, PDF_SIGNATURE, b"MZ", b"\x7fELF", b"\x1f\x8b", b"\x89PNG", b"\xff\xd8\xff")

# The single source of truth for what RecliQ can actually parse.
SUPPORTED_FORMATS: dict[str, dict[str, str]] = {
    ".xlsx": {"label": "Excel workbook", "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".xls": {"label": "Excel 97-2003 workbook", "content_type": "application/vnd.ms-excel"},
    ".csv": {"label": "Comma-separated values", "content_type": "text/csv"},
    ".tsv": {"label": "Tab-separated values", "content_type": "text/tab-separated-values"},
    ".txt": {"label": "Comma-delimited text, or an MT940/BAI2 statement", "content_type": "text/plain"},
    ".pdf": {"label": "PDF (tables only)", "content_type": "application/pdf"},
    ".docx": {"label": "Word document (tables only)", "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".sta": {"label": "MT940 bank statement", "content_type": "text/plain"},
    ".mt940": {"label": "MT940 bank statement", "content_type": "text/plain"},
    ".940": {"label": "MT940 bank statement", "content_type": "text/plain"},
    ".bai": {"label": "BAI2 bank statement", "content_type": "text/plain"},
    ".bai2": {"label": "BAI2 bank statement", "content_type": "text/plain"},
    ".xml": {"label": "CAMT.053 bank statement (ISO 20022)", "content_type": "application/xml"},
    ".json": {"label": "GSTR-2B return (GST portal JSON)", "content_type": "application/json"},
}
_TABULAR_TEXT = {".csv", ".tsv", ".txt"}
# Finance formats are text too; their content is checked by parsing them.
_FINANCE_TEXT = {".sta", ".mt940", ".940", ".bai", ".bai2", ".xml", ".json"}
# Uncompressed content above this multiple of the upload limit is treated as a
# decompression bomb.
_MAX_EXPANSION = 20


class UploadRejected(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ValidatedUpload:
    extension: str
    content_type: str
    size_bytes: int
    row_counts: dict[str, int]


def supported_extensions_text() -> str:
    return ", ".join(sorted(SUPPORTED_FORMATS))


def _extension(filename: str | None) -> str:
    name = (filename or "").lower()
    return name[name.rfind("."):] if "." in name else ""


def _stream_size(stream: BinaryIO) -> int:
    stream.seek(0, io.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return size


def _check_zip_container(stream: BinaryIO, required_prefix: str, kind: str, max_bytes: int) -> None:
    try:
        with zipfile.ZipFile(stream) as archive:
            names = archive.namelist()
            if "[Content_Types].xml" not in names or not any(name.startswith(required_prefix) for name in names):
                raise UploadRejected(f"The file is a ZIP archive but not a valid {kind}.", 415)
            expanded = sum(info.file_size for info in archive.infolist())
            if expanded > max(max_bytes * _MAX_EXPANSION, 200 * 1024 * 1024):
                raise UploadRejected(f"The {kind} expands to an unsafe size and was rejected.", 413)
    except zipfile.BadZipFile as exc:
        raise UploadRejected(f"The file is damaged and is not a valid {kind}.", 415) from exc
    finally:
        stream.seek(0)


def _check_content(extension: str, head: bytes, stream: BinaryIO, max_bytes: int) -> None:
    if extension == ".xlsx":
        if head.startswith(OLE2_SIGNATURE):
            raise UploadRejected(
                "This workbook is password-protected or saved in the Excel 97-2003 format. "
                "Remove the password, or save it as .xls, and upload again.",
                415,
            )
        if not head.startswith(ZIP_SIGNATURE):
            raise UploadRejected("The file is named .xlsx but its content is not an Excel workbook.", 415)
        _check_zip_container(stream, "xl/", "Excel workbook", max_bytes)
    elif extension == ".xls":
        if head.startswith(ZIP_SIGNATURE):
            raise UploadRejected("The file is named .xls but is a newer Excel workbook. Rename it to .xlsx.", 415)
        if not head.startswith(OLE2_SIGNATURE):
            raise UploadRejected("The file is named .xls but its content is not an Excel 97-2003 workbook.", 415)
    elif extension == ".docx":
        if not head.startswith(ZIP_SIGNATURE):
            raise UploadRejected("The file is named .docx but its content is not a Word document.", 415)
        _check_zip_container(stream, "word/", "Word document", max_bytes)
    elif extension == ".pdf":
        if PDF_SIGNATURE not in head[:1024]:
            raise UploadRejected("The file is named .pdf but its content is not a PDF.", 415)
    elif extension in _TABULAR_TEXT or extension in _FINANCE_TEXT:
        if any(head.startswith(signature) for signature in _BINARY_SIGNATURES) or b"\x00" in head:
            kind = "delimited text" if extension in _TABULAR_TEXT else SUPPORTED_FORMATS[extension]["label"]
            raise UploadRejected(f"The file is named {extension} but contains binary data, not {kind}.", 415)


def _finance_row_counts(extension: str, head: bytes, stream: BinaryIO) -> dict[str, int] | None:
    """Parse a bank statement or GST return now, so a damaged or mislabelled
    file is refused at upload with the parser's reason. None: not finance."""
    from app.reconciliation_engine.ingestion.finance_formats import FORMAT_LABELS, FinanceFormatError, format_for, parse_bytes

    fmt = format_for(extension, head)
    if fmt is None:
        return None
    try:
        sheets = parse_bytes(stream.read(), fmt)
    except FinanceFormatError as exc:
        raise UploadRejected(f"This {FORMAT_LABELS[fmt]} could not be read: {exc}", 415) from exc
    finally:
        stream.seek(0)
    return {name: len(frame) + 1 for name, frame in sheets.items()}  # +1: counted like a header row.


def _count_text_rows(stream: BinaryIO) -> int:
    rows, last = 0, b""
    while chunk := stream.read(1024 * 1024):
        rows += chunk.count(b"\n")
        last = chunk[-1:]
    stream.seek(0)
    return rows + (1 if last and last != b"\n" else 0)


def _row_counts(extension: str, stream: BinaryIO) -> dict[str, int]:
    """Rows per sheet, read from workbook metadata without parsing cell data."""
    if extension == ".xlsx":
        from openpyxl import load_workbook

        try:
            workbook = load_workbook(stream, read_only=True, data_only=True)
        except Exception as exc:
            raise UploadRejected(f"The Excel workbook could not be opened: {exc}", 415) from exc
        try:
            return {sheet.title: int(sheet.max_row or 0) for sheet in workbook.worksheets}
        finally:
            workbook.close()
            stream.seek(0)
    if extension == ".xls":
        import xlrd

        try:
            workbook = xlrd.open_workbook(file_contents=stream.read(), on_demand=True)
        except Exception as exc:
            raise UploadRejected(f"The Excel 97-2003 workbook could not be opened: {exc}", 415) from exc
        finally:
            stream.seek(0)
        try:
            return {name: workbook.sheet_by_name(name).nrows for name in workbook.sheet_names()}
        finally:
            workbook.release_resources()
    if extension in _TABULAR_TEXT:
        return {"Main Data": _count_text_rows(stream)}
    return {}  # PDF/Word tables are bounded by the file-size limit.


def validate_upload(filename: str | None, stream: BinaryIO, *, max_bytes: int, max_rows: int) -> ValidatedUpload:
    extension = _extension(filename)
    if extension not in SUPPORTED_FORMATS:
        shown = extension or "files without an extension"
        raise UploadRejected(f"Unsupported file format ({shown}). Supported formats: {supported_extensions_text()}.", 415)

    size = _stream_size(stream)
    if size == 0:
        raise UploadRejected("The file is empty.", 400)
    if size > max_bytes:
        raise UploadRejected(
            f"The file is {size / (1024 * 1024):.1f} MB; the limit is {max_bytes // (1024 * 1024)} MB.", 413
        )

    head = stream.read(8192)
    stream.seek(0)
    _check_content(extension, head, stream, max_bytes)

    row_counts = _finance_row_counts(extension, head, stream)
    if row_counts is None:
        row_counts = _row_counts(extension, stream)
    for sheet, rows in row_counts.items():
        data_rows = max(0, rows - 1)  # The first row is the header.
        if data_rows > max_rows:
            raise UploadRejected(
                f"Sheet '{sheet}' has {data_rows:,} data rows; the limit is {max_rows:,} rows per sheet.", 413
            )
    return ValidatedUpload(extension, SUPPORTED_FORMATS[extension]["content_type"], size, row_counts)


class BodySizeLimitMiddleware:
    """Reject request bodies above a limit while they stream in, so an
    oversized upload is refused before it is spooled to disk in full."""

    def __init__(self, app, max_bytes: int, paths: tuple[str, ...]):
        self.app = app
        self.max_bytes = max_bytes
        self.paths = paths

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not any(scope["path"].endswith(path) for path in self.paths):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(send)
            return

        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not response_started:
                await self._reject(send)

    async def _reject(self, send) -> None:
        import json

        body = json.dumps({"detail": f"The upload exceeds the {self.max_bytes // (1024 * 1024)} MB limit."}).encode()
        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})


class _BodyTooLarge(Exception):
    pass
