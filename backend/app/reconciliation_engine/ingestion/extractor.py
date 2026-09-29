import hashlib
import os
import tempfile
import threading
from collections import OrderedDict
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import docx
except ImportError:
    docx = None


def get_file_extension(filename: str) -> str:
    return os.path.splitext(filename)[1].lower()


_format_cache: "OrderedDict[tuple, str | None]" = OrderedDict()


def finance_format(file_path: Path, filename: str) -> str | None:
    """MT940/CAMT.053/BAI2/GSTR-2B, or None for an ordinary table.

    ``.txt`` files are sniffed, since banks deliver MT940 and BAI2 as .txt.
    """
    from app.reconciliation_engine.ingestion import finance_formats

    ext = get_file_extension(filename)
    if ext not in finance_formats.EXTENSION_FORMATS and ext != ".txt":
        return None
    key = _cache_key(Path(file_path), "__format__")
    if key is not None and key in _format_cache:
        return _format_cache[key]
    try:
        with open(file_path, "rb") as handle:
            head = handle.read(8192)
    except OSError:
        return None
    fmt = finance_formats.format_for(ext, head)
    if key is not None:
        _format_cache[key] = fmt
        while len(_format_cache) > 256:
            _format_cache.popitem(last=False)
    return fmt


# .xls (Excel 97-2003) needs xlrd; pandas' default engine only reads .xlsx.
_EXCEL_ENGINES = {".xlsx": "openpyxl", ".xls": "xlrd"}


def _max_rows() -> int | None:
    try:
        from app.core.config import get_settings

        return get_settings().max_rows_per_sheet
    except Exception:
        return None


def _enforce_row_limit(df: pd.DataFrame, sheet_id: str) -> pd.DataFrame:
    """Backstop for the upload-time check (e.g. PDF/Word tables)."""
    limit = _max_rows()
    if limit and len(df) > limit:
        raise ValueError(f"Sheet '{sheet_id}' has {len(df):,} rows; the limit is {limit:,} rows per sheet.")
    return df


def extract_file_metadata(file_path: Path, filename: str) -> List[Dict[str, Any]]:
    """
    Returns a list of available 'sheets' or 'tables' within the file.
    For CSV, it's just one table.
    For Excel, it's the sheet names.
    For PDF/Word, it attempts to find tables.
    """
    ext = get_file_extension(filename)

    if fmt := finance_format(file_path, filename):
        from app.reconciliation_engine.ingestion.finance_formats import sheet_names

        return [{"id": name, "name": name} for name in sheet_names(fmt)]

    if ext in _EXCEL_ENGINES:
        try:
            with pd.ExcelFile(file_path, engine=_EXCEL_ENGINES[ext]) as xl:
                return [{"id": sheet, "name": sheet} for sheet in xl.sheet_names]
        except Exception as e:
            raise ValueError(f"Failed to read Excel file: {e}")
            
    elif ext in ['.csv', '.tsv', '.txt']:
        # For CSV/TSV, there is only one "sheet".
        return [{"id": "default", "name": "Main Data"}]
        
    elif ext == '.pdf':
        if pdfplumber is None:
            raise ImportError("pdfplumber is required to parse PDFs. Please install it.")
        tables = []
        try:
            with pdfplumber.open(file_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    page_tables = page.find_tables()
                    for j, _ in enumerate(page_tables):
                        tables.append({
                            "id": f"page_{i+1}_table_{j+1}",
                            "name": f"Page {i+1} - Table {j+1}"
                        })
        except Exception as e:
            raise ValueError(f"Failed to parse PDF: {e}")
            
        if not tables:
            raise ValueError("No tables detected in PDF.")
        return tables
        
    elif ext == '.docx':
        if docx is None:
            raise ImportError("python-docx is required to parse Word documents.")
        try:
            doc = docx.Document(file_path)
            tables = []
            for i, table in enumerate(doc.tables):
                # Basic check if it looks like a data table (has rows)
                if len(table.rows) > 0:
                    tables.append({
                        "id": f"table_{i+1}",
                        "name": f"Table {i+1} ({len(table.rows)} rows)"
                    })
            if not tables:
                raise ValueError("No data tables detected in Word document.")
            return tables
        except Exception as e:
            raise ValueError(f"Failed to parse Word document: {e}")
            
    else:
        raise ValueError(f"Unsupported file format: {ext}")


# ── Parsed-sheet cache ─────────────────────────────────────────────────────
# Parsing a large workbook dominates setup time (a 50k-row sheet takes ~12s
# with openpyxl), and one reconciliation reads each sheet several times
# (analysis, columns, execution). Each sheet is parsed once, kept in a small
# in-memory LRU and in a pickle beside the upload so other workers reuse it.
_CACHEABLE_EXTENSIONS = {".xlsx", ".xls", ".csv", ".txt", ".tsv", ".sta", ".mt940", ".940", ".bai", ".bai2", ".xml", ".json"}
_MEMORY_CACHE_SIZE = 6
_memory_cache: "OrderedDict[tuple, pd.DataFrame]" = OrderedDict()
_cache_guard = threading.Lock()
_parse_locks: dict[tuple, threading.Lock] = {}


def _cache_path(file_path: Path, sheet_id: str) -> Path:
    digest = hashlib.sha1(str(sheet_id).encode("utf-8")).hexdigest()[:12]
    return file_path.with_name(f"{file_path.name}.sheet-{digest}.pkl")


def _cache_key(file_path: Path, sheet_id: str) -> tuple | None:
    try:
        stat = file_path.stat()
    except OSError:
        return None
    return (str(file_path.resolve()), stat.st_mtime_ns, stat.st_size, str(sheet_id))


def _write_pickle(df: pd.DataFrame, disk_path: Path) -> None:
    """Atomic write: a concurrent reader never sees a half-written cache."""
    partial = disk_path.with_name(f"{disk_path.name}.{os.getpid()}.part")
    df.to_pickle(partial)
    os.replace(partial, disk_path)


def _remember(key: tuple, df: pd.DataFrame) -> None:
    with _cache_guard:
        _memory_cache[key] = df
        _memory_cache.move_to_end(key)
        while len(_memory_cache) > _MEMORY_CACHE_SIZE:
            _memory_cache.popitem(last=False)


def read_table_data(file_path: Path, filename: str, sheet_id: str) -> pd.DataFrame:
    """Read one sheet/table, parsing each workbook sheet at most once.

    Callers receive a copy, so mutating the result never alters the cache.
    """
    key = _cache_key(file_path, sheet_id) if get_file_extension(filename) in _CACHEABLE_EXTENSIONS else None
    if key is None:
        return _parse_table_data(file_path, filename, sheet_id)

    with _cache_guard:
        cached = _memory_cache.get(key)
        lock = _parse_locks.setdefault(key, threading.Lock())
    if cached is not None:
        return cached.copy()

    # One parse per sheet: concurrent requests (e.g. the upload warm-up and an
    # analysis call) wait for the first parse instead of repeating it.
    with lock:
        with _cache_guard:
            cached = _memory_cache.get(key)
        if cached is not None:
            return cached.copy()
        disk_path = _cache_path(file_path, sheet_id)
        df = None
        if not disk_path.exists():
            _await_warm_up(file_path)
        if disk_path.exists() and disk_path.stat().st_mtime_ns >= key[1]:
            try:
                df = pd.read_pickle(disk_path)
            except Exception:
                df = None
        if df is None:
            df = _parse_table_data(file_path, filename, sheet_id)
            try:
                _write_pickle(df, disk_path)
            except OSError:
                pass  # Read-only or temporary storage: memory cache only.
        _remember(key, df)
    return df.copy()


def read_table_sample(file_path: Path, filename: str, sheet_id: str, nrows: int) -> pd.DataFrame:
    """First ``nrows`` rows of a sheet, without waiting for a full parse.

    Setup analysis only needs a sample; reading it directly takes about a
    second even while the full sheet is still being parsed in the background.
    """
    key = _cache_key(file_path, sheet_id)
    with _cache_guard:
        cached = _memory_cache.get(key) if key else None
    if cached is not None:
        return cached.head(nrows).copy()
    disk_path = _cache_path(file_path, sheet_id)
    if key and disk_path.exists() and disk_path.stat().st_mtime_ns >= key[1]:
        try:
            return pd.read_pickle(disk_path).head(nrows)
        except Exception:
            pass
    ext = get_file_extension(filename)
    if ext in _EXCEL_ENGINES:
        return pd.read_excel(file_path, sheet_name=sheet_id, nrows=nrows, engine=_EXCEL_ENGINES[ext])
    if ext in [".csv", ".txt", ".tsv"] and not finance_format(file_path, filename):
        return pd.read_csv(file_path, sep="\t" if ext == ".tsv" else ",", nrows=nrows)
    return read_table_data(file_path, filename, sheet_id).head(nrows)


def read_table_columns(file_path: Path, filename: str, sheet_id: str) -> list[str]:
    """Return a sheet's header row without parsing its data rows when possible."""
    key = _cache_key(file_path, sheet_id)
    with _cache_guard:
        cached = _memory_cache.get(key) if key else None
    if cached is not None:
        return [str(column) for column in cached.columns]
    ext = get_file_extension(filename)
    if ext in _EXCEL_ENGINES:
        return [str(column) for column in pd.read_excel(file_path, sheet_name=sheet_id, nrows=0, engine=_EXCEL_ENGINES[ext]).columns]
    if ext in [".csv", ".txt", ".tsv"] and not finance_format(file_path, filename):
        return [str(column) for column in pd.read_csv(file_path, sep="\t" if ext == ".tsv" else ",", nrows=0).columns]
    return [str(column) for column in read_table_data(file_path, filename, sheet_id).columns]


def _parse_sheets_to_disk(file_path: str, filename: str) -> None:
    """Worker-process entry point: write every sheet's parsed pickle."""
    path = Path(file_path)
    for sheet in extract_file_metadata(path, filename):
        disk_path = _cache_path(path, sheet["id"])
        if not disk_path.exists():
            _write_pickle(_parse_table_data(path, filename, sheet["id"]), disk_path)


# Parsing is CPU-bound. In a thread it would hold the GIL and slow every
# interactive request (sheet lists, headers, analysis) while it runs, so
# warm-up runs in a separate process that only writes the disk cache.
_warm_pool: ProcessPoolExecutor | None = None
_warm_jobs: dict[str, Future] = {}


def warm_table_cache(file_path: Path, filename: str) -> None:
    """Parse every sheet of an upload in a background process, ahead of use."""
    global _warm_pool
    try:
        with _cache_guard:
            if _warm_pool is None:
                _warm_pool = ProcessPoolExecutor(max_workers=2)
            _warm_jobs[str(file_path.resolve())] = _warm_pool.submit(_parse_sheets_to_disk, str(file_path), filename)
    except Exception:
        pass  # Warm-up is best effort; reads parse on demand.


def _await_warm_up(file_path: Path, timeout: float = 120) -> None:
    """If a warm-up is parsing this file, wait for it instead of parsing twice."""
    job = _warm_jobs.get(str(file_path.resolve()))
    if job is not None:
        try:
            job.result(timeout=timeout)
        except Exception:
            pass


def clear_table_cache(file_path: Path) -> None:
    """Drop on-disk parsed sheets when their upload is deleted."""
    for cached in file_path.parent.glob(f"{file_path.name}.sheet-*.pkl"):
        cached.unlink(missing_ok=True)


def _parse_table_data(file_path: Path, filename: str, sheet_id: str) -> pd.DataFrame:
    """Read one sheet/table and apply the configured row limit."""
    return _enforce_row_limit(_parse_table_unchecked(file_path, filename, sheet_id), sheet_id)


def _parse_table_unchecked(file_path: Path, filename: str, sheet_id: str) -> pd.DataFrame:
    ext = get_file_extension(filename)

    if fmt := finance_format(file_path, filename):
        from app.reconciliation_engine.ingestion.finance_formats import parse_file

        sheets = parse_file(file_path, fmt)
        if sheet_id not in sheets:
            raise ValueError(f"Sheet '{sheet_id}' does not exist in this file. Available: {', '.join(sheets)}.")
        return sheets[sheet_id]

    if ext in _EXCEL_ENGINES:
        return pd.read_excel(file_path, sheet_name=sheet_id, engine=_EXCEL_ENGINES[ext])
        
    elif ext in ['.csv', '.txt']:
        return pd.read_csv(file_path)
        
    elif ext == '.tsv':
        return pd.read_csv(file_path, sep='\t')
        
    elif ext == '.pdf':
        if pdfplumber is None:
            raise ImportError("pdfplumber is required")
            
        try:
            # sheet_id format: page_{i}_table_{j}
            parts = sheet_id.split('_')
            page_idx = int(parts[1]) - 1
            table_idx = int(parts[3]) - 1
            
            with pdfplumber.open(file_path) as pdf:
                page = pdf.pages[page_idx]
                tables = page.extract_tables()
                if table_idx < len(tables):
                    table_data = tables[table_idx]
                    if not table_data or len(table_data) < 2:
                        return pd.DataFrame() # empty or no headers
                    
                    # Assume first row is header
                    df = pd.DataFrame(table_data[1:], columns=table_data[0])
                    return df
                else:
                    raise ValueError(f"Table index {table_idx} out of range for page {page_idx+1}")
        except Exception as e:
            raise ValueError(f"Failed to extract PDF table: {e}")
            
    elif ext == '.docx':
        if docx is None:
            raise ImportError("python-docx is required")
            
        try:
            parts = sheet_id.split('_')
            table_idx = int(parts[1]) - 1
            
            doc = docx.Document(file_path)
            if table_idx < len(doc.tables):
                table = doc.tables[table_idx]
                
                data = []
                for row in table.rows:
                    data.append([cell.text for cell in row.cells])
                    
                if not data or len(data) < 2:
                    return pd.DataFrame()
                    
                df = pd.DataFrame(data[1:], columns=data[0])
                return df
            else:
                raise ValueError(f"Table index {table_idx} out of range")
        except Exception as e:
            raise ValueError(f"Failed to extract Word table: {e}")
            
    else:
        raise ValueError(f"Unsupported file format: {ext}")
