"""Compare reconciliation result sets without depending on record order.

Usage from the repository root:

    backend/.venv/Scripts/python scripts/perf/result_comparison.py old.json new.json

The JSON inputs should be the universal result data returned by the engine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


UNORDERED_RECORD_KEYS = {
    "exception_summary": ("Exception Type",),
    "field_exception_summary": ("Field",),
    "exceptions": ("Primary Key", "Matched Key", "Field", "Secondary Key"),
    "matched_records": ("ROW (FILE 1)", "ROW (FILE 2)"),
    "missing_in_file_1": ("ROW (FILE 2)",),
    "missing_in_file_2": ("ROW (FILE 1)",),
    "identity_resolution": ("ROW (FILE 1)",),
    "control_checks": ("Control",),
}


@dataclass(frozen=True)
class Difference:
    path: str
    kind: str
    expected: Any = None
    actual: Any = None


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> bytes:
    return hashlib.sha256(_canonical(value).encode("utf-8")).digest()


def _record_identity(record: dict[str, Any], fields: tuple[str, ...]) -> str | None:
    identity = {field: record[field] for field in fields if field in record}
    return _canonical(identity) if identity else None


def _record_sort_key(record: dict[str, Any], identity_fields: tuple[str, ...]) -> tuple[str, str]:
    return _record_identity(record, identity_fields) or "", _canonical(record)


def _compare_record_lists(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    path: str,
    identity_fields: tuple[str, ...],
) -> list[Difference]:
    differences: list[Difference] = []

    actual_counts = Counter(_fingerprint(record) for record in actual)
    expected_remaining: list[dict[str, Any]] = []
    for record in expected:
        key = _fingerprint(record)
        if actual_counts[key]:
            actual_counts[key] -= 1
        else:
            expected_remaining.append(record)

    actual_remaining: list[dict[str, Any]] = []
    remaining_counts = actual_counts.copy()
    for record in actual:
        key = _fingerprint(record)
        if remaining_counts[key]:
            remaining_counts[key] -= 1
            actual_remaining.append(record)

    expected_remaining.sort(key=lambda record: _record_sort_key(record, identity_fields))
    actual_remaining.sort(key=lambda record: _record_sort_key(record, identity_fields))

    actual_by_identity: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, record in enumerate(actual_remaining):
        identity = _record_identity(record, identity_fields)
        if identity is not None:
            actual_by_identity[identity].append((index, record))
    for records in actual_by_identity.values():
        records.sort(key=lambda item: _canonical(item[1]))

    paired_expected: set[int] = set()
    paired_actual_indices: set[int] = set()
    for index, expected_record in enumerate(expected_remaining):
        identity = _record_identity(expected_record, identity_fields)
        candidates = actual_by_identity.get(identity, []) if identity is not None else []
        if not candidates:
            continue
        actual_index, actual_record = candidates.pop(0)
        paired_expected.add(index)
        paired_actual_indices.add(actual_index)
        identity_label = identity if identity is not None else "{}"
        differences.extend(
            _compare_values(
                expected_record,
                actual_record,
                f"{path}[identity={identity_label}]",
                (path,),
            )
        )

    for index, record in enumerate(expected_remaining):
        if index not in paired_expected:
            differences.append(Difference(f"{path}[expected-only:{index}]", "record_missing_from_actual", record, None))
    for index, record in enumerate(actual_remaining):
        if index not in paired_actual_indices:
            differences.append(Difference(f"{path}[actual-only:{index}]", "record_missing_from_expected", None, record))

    return differences


def _compare_values(
    expected: Any,
    actual: Any,
    path: str,
    path_parts: tuple[str, ...],
) -> list[Difference]:
    if path_parts == ("metadata", "generated_at"):
        return []

    if isinstance(expected, dict) and isinstance(actual, dict):
        differences: list[Difference] = []
        for key in sorted(set(expected) | set(actual), key=str):
            if path_parts == ("metadata",) and key in {"generated_at", "engine", "engine_version"}:
                continue
            child_path = f"{path}.{key}" if path else str(key)
            if key not in expected:
                differences.append(Difference(child_path, "field_missing_from_expected", None, actual[key]))
            elif key not in actual:
                differences.append(Difference(child_path, "field_missing_from_actual", expected[key], None))
            else:
                differences.extend(_compare_values(expected[key], actual[key], child_path, (*path_parts, str(key))))
        return differences

    if isinstance(expected, list) and isinstance(actual, list):
        collection = path_parts[-1] if path_parts else ""
        identity_fields = UNORDERED_RECORD_KEYS.get(collection)
        if identity_fields and all(isinstance(item, dict) for item in expected) and all(
            isinstance(item, dict) for item in actual
        ):
            return _compare_record_lists(expected, actual, path, identity_fields)

        differences = []
        if len(expected) != len(actual):
            differences.append(Difference(path, "list_length_mismatch", len(expected), len(actual)))
        for index, (left, right) in enumerate(zip(expected, actual)):
            differences.extend(_compare_values(left, right, f"{path}[{index}]", (*path_parts, str(index))))
        return differences

    if type(expected) is not type(actual):
        return [Difference(path, "type_mismatch", type(expected).__name__, type(actual).__name__)]
    if expected != actual:
        return [Difference(path, "value_mismatch", expected, actual)]
    return []


def compare_result_sets(expected: Any, actual: Any) -> list[Difference]:
    """Return every semantic difference, ignoring output order for known record lists."""
    differences = _compare_values(expected, actual, "", ())
    return sorted(differences, key=lambda item: (item.path, item.kind, _canonical(item.expected), _canonical(item.actual)))


class _JsonStreamReader:
    """Incrementally decode JSON values without buffering an entire large array."""

    def __init__(self, path: Path) -> None:
        self._file = path.open("r", encoding="utf-8", buffering=1024 * 1024)
        self._decoder = json.JSONDecoder()
        self._buffer = ""
        self._position = 0
        self._eof = False

    def close(self) -> None:
        self._file.close()

    def _fill(self) -> bool:
        if self._eof:
            return False
        chunk = self._file.read(1024 * 1024)
        if not chunk:
            self._eof = True
            return False
        self._buffer += chunk
        return True

    def _compact(self) -> None:
        if self._position >= 1024 * 1024 or self._position == len(self._buffer):
            self._buffer = self._buffer[self._position :]
            self._position = 0

    def _skip_whitespace(self) -> None:
        while True:
            while self._position < len(self._buffer) and self._buffer[self._position] in " \t\r\n":
                self._position += 1
            if self._position < len(self._buffer) or not self._fill():
                self._compact()
                return

    def peek(self) -> str:
        self._skip_whitespace()
        if self._position >= len(self._buffer):
            return ""
        return self._buffer[self._position]

    def consume(self, expected: str) -> None:
        actual = self.peek()
        if actual != expected:
            raise ValueError(f"Invalid JSON in {self._file.name}: expected {expected!r}, got {actual!r}")
        self._position += 1
        self._compact()

    def read_value(self) -> Any:
        self._skip_whitespace()
        while True:
            try:
                value, end = self._decoder.raw_decode(self._buffer, self._position)
            except json.JSONDecodeError:
                if not self._fill():
                    raise
                continue
            self._position = end
            self._compact()
            return value

    def ensure_eof(self) -> None:
        if self.peek():
            raise ValueError(f"Unexpected content after JSON object in {self._file.name}")


def compare_result_files(
    expected_path: Path,
    actual_path: Path,
    *,
    sample_limit: int = 20,
) -> tuple[int, list[Difference]]:
    """Compare saved results with bounded memory, streaming large record arrays to SQLite."""
    with tempfile.TemporaryDirectory(prefix="recliq-result-parity-") as temp_dir:
        connection = sqlite3.connect(Path(temp_dir) / "record-hashes.sqlite3")
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute("CREATE TABLE record_hashes (side INTEGER, collection TEXT, digest BLOB)")

        def read_document(path: Path, side: int) -> tuple[dict[str, Any], set[str]]:
            reader = _JsonStreamReader(path)
            ordinary: dict[str, Any] = {}
            streamed: set[str] = set()
            pending: list[tuple[int, str, bytes]] = []
            try:
                reader.consume("{")
                if reader.peek() == "}":
                    reader.consume("}")
                    reader.ensure_eof()
                    return ordinary, streamed
                while True:
                    key = reader.read_value()
                    if not isinstance(key, str):
                        raise ValueError(f"Expected a JSON object key in {path}")
                    reader.consume(":")
                    if key in UNORDERED_RECORD_KEYS and reader.peek() == "[":
                        streamed.add(key)
                        reader.consume("[")
                        if reader.peek() != "]":
                            while True:
                                record = reader.read_value()
                                pending.append((side, key, _fingerprint(record)))
                                if len(pending) >= 4096:
                                    connection.executemany(
                                        "INSERT INTO record_hashes VALUES (?, ?, ?)", pending
                                    )
                                    pending.clear()
                                delimiter = reader.peek()
                                if delimiter == "]":
                                    break
                                reader.consume(",")
                        reader.consume("]")
                    else:
                        ordinary[key] = reader.read_value()

                    delimiter = reader.peek()
                    if delimiter == "}":
                        reader.consume("}")
                        break
                    reader.consume(",")
                if pending:
                    connection.executemany("INSERT INTO record_hashes VALUES (?, ?, ?)", pending)
                reader.ensure_eof()
                connection.commit()
                return ordinary, streamed
            finally:
                reader.close()

        expected, expected_streamed = read_document(expected_path, 0)
        actual, actual_streamed = read_document(actual_path, 1)
        differences = _compare_values(expected, actual, "", ())
        difference_count = len(differences)
        samples = differences[:sample_limit]

        for collection in sorted(expected_streamed | actual_streamed):
            if collection not in expected_streamed:
                difference = Difference(collection, "field_missing_from_expected", None, "record list")
                difference_count += 1
                if len(samples) < sample_limit:
                    samples.append(difference)
            elif collection not in actual_streamed:
                difference = Difference(collection, "field_missing_from_actual", "record list", None)
                difference_count += 1
                if len(samples) < sample_limit:
                    samples.append(difference)
            else:
                rows = connection.execute(
                    """
                    SELECT digest,
                           SUM(CASE WHEN side = 0 THEN 1 ELSE 0 END),
                           SUM(CASE WHEN side = 1 THEN 1 ELSE 0 END)
                    FROM record_hashes
                    WHERE collection = ?
                    GROUP BY digest
                    HAVING SUM(CASE WHEN side = 0 THEN 1 ELSE 0 END)
                        != SUM(CASE WHEN side = 1 THEN 1 ELSE 0 END)
                    ORDER BY digest
                    """,
                    (collection,),
                )
                for digest, expected_count, actual_count in rows:
                    difference_count += 1
                    if len(samples) < sample_limit:
                        samples.append(Difference(
                            f"{collection}[sha256={bytes(digest).hex()}]",
                            "record_count_mismatch",
                            expected_count,
                            actual_count,
                        ))
        samples.sort(key=lambda item: (item.path, item.kind, _canonical(item.expected), _canonical(item.actual)))
        connection.close()
        return difference_count, samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected", type=Path, help="JSON universal result from the baseline engine")
    parser.add_argument("actual", type=Path, help="JSON universal result from the candidate engine")
    args = parser.parse_args(argv)

    difference_count, differences = compare_result_files(args.expected, args.actual)
    print(json.dumps({
        "identical": difference_count == 0,
        "difference_count": difference_count,
        "differences": [asdict(item) for item in differences],
    }, ensure_ascii=False, indent=2))
    return 0 if difference_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
