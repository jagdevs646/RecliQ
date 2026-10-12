"""Parquet persistence and bounded queries for reconciliation result rows."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from app.storage.base import REPORT_DATA_CATEGORIES
from app.utils.json_encoder import safe_json_dumps, safe_json_dump

_BATCH_SIZE = 5_000
_HIDDEN_COLUMNS = {"File Pair ID", "Sheet Rule ID"}
_HIDDEN_PREFIXES = ("NORM_", "MATCHED NORM_", "__")
_READ_BATCH_SIZE = 5_000


def _visible(column: str) -> bool:
    return column not in _HIDDEN_COLUMNS and not column.startswith(_HIDDEN_PREFIXES)


def _row_search_text(record: dict[str, Any]) -> str:
    return " ".join(
        str(value).casefold()
        for column, value in record.items()
        if _visible(str(column)) and value is not None
    )


def write_report_tables(universal_data: dict[str, Any], directory: Path) -> dict[str, Any]:
    """Write category rows in bounded batches and a compact reconstruction manifest."""
    directory.mkdir(parents=True, exist_ok=True)
    category_facts: dict[str, Any] = {}
    schema = pa.schema([
        ("row_order", pa.int64()),
        ("file_pair_id", pa.string()),
        ("sheet_rule_id", pa.string()),
        ("search_text", pa.string()),
        ("record_json", pa.string()),
    ])

    for category in REPORT_DATA_CATEGORIES:
        records = universal_data.get(category) or []
        path = directory / f"{category}.parquet"
        columns: dict[str, None] = {}
        scope_counts: dict[str, int] = {}
        writer = pq.ParquetWriter(path, schema, compression="zstd", use_dictionary=True)
        try:
            for start in range(0, len(records), _BATCH_SIZE):
                batch = records[start:start + _BATCH_SIZE]
                values: dict[str, list[Any]] = {name: [] for name in schema.names}
                for row_index, record in enumerate(batch, start=start):
                    columns.update((str(column), None) for column in record)
                    pair_id = str(record.get("File Pair ID") or "")
                    rule_id = str(record.get("Sheet Rule ID") or "")
                    scope_key = f"{pair_id}\u001f{rule_id}"
                    scope_counts[scope_key] = scope_counts.get(scope_key, 0) + 1
                    values["row_order"].append(row_index)
                    values["file_pair_id"].append(pair_id)
                    values["sheet_rule_id"].append(rule_id)
                    values["search_text"].append(_row_search_text(record))
                    values["record_json"].append(safe_json_dumps(record, separators=(",", ":")))
                writer.write_table(pa.Table.from_pydict(values, schema=schema), row_group_size=_BATCH_SIZE)
        finally:
            writer.close()
        category_facts[category] = {
            "file": path.name,
            "row_count": len(records),
            "columns": [column for column in columns if _visible(column)],
            "scope_counts": scope_counts,
        }

    manifest = {
        "schema_version": 1,
        "record_categories": category_facts,
        "universal_data": {
            key: value
            for key, value in universal_data.items()
            if key not in REPORT_DATA_CATEGORIES
        },
    }
    manifest_path = directory / "summary.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        safe_json_dump(manifest, handle, ensure_ascii=False, separators=(",", ":"))
    manifest["size_bytes"] = sum(path.stat().st_size for path in directory.iterdir() if path.is_file())
    return manifest


class ParquetRecordRows:
    """A repeatable, bounded-memory view over one stored result category."""

    def __init__(self, path: Path, row_count: int, file_pair_id: str | None = None):
        self.path = path
        self.row_count = row_count
        self.file_pair_id = file_pair_id

    def __len__(self) -> int:
        if self.file_pair_id is None:
            return self.row_count
        with duckdb.connect(config={"threads": "1", "memory_limit": "256MB"}) as connection:
            return int(connection.execute(
                "SELECT count(*) FROM read_parquet(?) WHERE file_pair_id = ?",
                [str(self.path), self.file_pair_id],
            ).fetchone()[0])

    def __iter__(self) -> Iterator[dict[str, Any]]:
        query = "SELECT record_json FROM read_parquet(?)"
        params: list[Any] = [str(self.path)]
        if self.file_pair_id is not None:
            query += " WHERE file_pair_id = ?"
            params.append(self.file_pair_id)
        query += " ORDER BY row_order"
        connection = duckdb.connect(config={"threads": "1", "memory_limit": "256MB"})
        try:
            cursor = connection.execute(query, params)
            while batch := cursor.fetchmany(_READ_BATCH_SIZE):
                for (record_json,) in batch:
                    yield json.loads(record_json)
        finally:
            connection.close()

    def for_file_pair(self, file_pair_id: str) -> "ParquetRecordRows":
        return ParquetRecordRows(self.path, self.row_count, file_pair_id)


def read_report_tables(directory: Path, file_pair_id: str | None = None) -> dict[str, Any]:
    """Read report metadata and expose result rows as lazy Parquet iterators."""
    manifest_path = directory / "summary.json"
    with manifest_path.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    universal_data = manifest.get("universal_data") or {}
    if file_pair_id is not None:
        pair = (universal_data.get("file_pair_reports") or {}).get(file_pair_id)
        if pair is None:
            raise KeyError(file_pair_id)
        data = dict(pair)
    else:
        data = dict(universal_data)
    for category in REPORT_DATA_CATEGORIES:
        category_file = directory / manifest["record_categories"][category]["file"]
        facts = manifest["record_categories"][category]
        rows = ParquetRecordRows(category_file, int(facts.get("row_count", 0)))
        data[category] = rows.for_file_pair(file_pair_id) if file_pair_id is not None else rows
    return data


def query_report_category(
    directory: Path,
    category: str,
    *,
    offset: int = 0,
    limit: int = 25,
    file_pair_id: str | None = None,
    sheet_rule_id: str | None = None,
    classifications: set[str] | None = None,
    exclude_exact_matches: bool = False,
    search: str = "",
    sort_by: str | None = None,
    sort_direction: str = "asc",
) -> dict[str, Any]:
    """Filter and page one Parquet category without materializing its rows."""
    with (directory / "summary.json").open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    facts = manifest["record_categories"].get(category)
    if facts is None:
        raise FileNotFoundError(category)
    path = directory / facts["file"]
    clauses = ["1 = 1"]
    params: list[Any] = []
    if file_pair_id:
        clauses.append("file_pair_id = ?")
        params.append(file_pair_id)
    if sheet_rule_id:
        clauses.append("sheet_rule_id = ?")
        params.append(sheet_rule_id)
    classification_expr = "json_extract_string(record_json, '$.\"IDENTITY CLASSIFICATION\"')"
    if exclude_exact_matches:
        clauses.append(f"coalesce({classification_expr}, '') <> 'EXACT_MATCH'")
    elif classifications:
        clauses.append(f"{classification_expr} IN ({', '.join('?' for _ in classifications)})")
        params.extend(sorted(classifications))
    if search.strip():
        clauses.append("contains(search_text, ?)")
        params.append(search.strip().casefold())
    where = " AND ".join(clauses)
    source = "read_parquet(?)"

    scope_key = f"{file_pair_id or ''}\u001f{sheet_rule_id or ''}"
    category_count = int(facts.get("row_count", 0))
    scope_count = facts.get("scope_counts", {}).get(scope_key) if (file_pair_id or sheet_rule_id) else category_count
    if not search.strip() and not classifications and not exclude_exact_matches and scope_count is not None:
        total = int(scope_count)
    else:
        count_params = [str(path), *params]
        with duckdb.connect(config={"threads": "1", "memory_limit": "256MB"}) as connection:
            total = int(connection.execute(f"SELECT count(*) FROM {source} WHERE {where}", count_params).fetchone()[0])

    columns = facts.get("columns", [])
    if sort_by and sort_by not in columns:
        raise ValueError("The selected sort column is not available in this result.")
    order_sql = "row_order ASC"
    if sort_by:
        json_path = '$."' + sort_by.replace("\\", "\\\\").replace('"', '\\"') + '"'
        value_expr = "json_extract_string(record_json, ?)"
        direction = "DESC" if sort_direction.lower() == "desc" else "ASC"
        order_sql = f"try_cast({value_expr} AS DOUBLE) {direction} NULLS LAST, lower({value_expr}) {direction} NULLS LAST, row_order ASC"
        order_params: list[Any] = [json_path, json_path]
    else:
        order_params = []
    query_params = [str(path), *params, *order_params, limit, offset]
    query = (
        f"SELECT record_json FROM {source} WHERE {where} "
        f"ORDER BY {order_sql} LIMIT ? OFFSET ?"
    )
    with duckdb.connect(config={"threads": "1", "memory_limit": "256MB"}) as connection:
        rows = connection.execute(query, query_params).fetchall()
    return {
        "columns": columns,
        "rows": [json.loads(row[0]) for row in rows],
        "total_rows": total,
    }
