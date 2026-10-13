"""Compare reconciliation result sets without depending on record order.

Usage from the repository root:

    backend/.venv/Scripts/python scripts/perf/result_comparison.py old.json new.json

The JSON inputs should be the universal result data returned by the engine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
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
            if path_parts == ("metadata",) and key == "generated_at":
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected", type=Path, help="JSON universal result from the baseline engine")
    parser.add_argument("actual", type=Path, help="JSON universal result from the candidate engine")
    args = parser.parse_args(argv)

    expected = json.loads(args.expected.read_text(encoding="utf-8"))
    actual = json.loads(args.actual.read_text(encoding="utf-8"))
    differences = compare_result_sets(expected, actual)
    print(json.dumps({
        "identical": not differences,
        "difference_count": len(differences),
        "differences": [asdict(item) for item in differences],
    }, ensure_ascii=False, indent=2))
    return 0 if not differences else 1


if __name__ == "__main__":
    raise SystemExit(main())
