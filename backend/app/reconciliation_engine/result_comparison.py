"""Semantic comparison of universal reconciliation results.

The row and column engines can order equivalent records differently.  This
module compares their reconciliation facts rather than their presentation
order, and deliberately ignores explanation wording that is generated only
when a report or preview needs it.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
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

_PRESENTATION_ONLY_RECORD_FIELDS = {
    "MATCH EXPLANATION",
    "__MATCH_EXPLANATION_CONTEXT__",
}
_PRESENTATION_ONLY_METADATA_FIELDS = {
    "__MATCH_EXPLANATION_METADATA__",
}


@dataclass(frozen=True)
class Difference:
    """One meaningful discrepancy between two engine result payloads."""

    path: str
    kind: str
    expected: Any = None
    actual: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "expected": self.expected,
            "actual": self.actual,
        }


def _is_record_sequence(value: Any) -> bool:
    # The column engine returns lazy record collections, not lists; both are
    # compared by their records, never by their container type.
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _without_presentation_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_presentation_fields(child)
            for key, child in value.items()
            if key not in _PRESENTATION_ONLY_RECORD_FIELDS
        }
    if _is_record_sequence(value):
        return [_without_presentation_fields(child) for child in value]
    return value


def _canonical(value: Any) -> str:
    return json.dumps(_without_presentation_fields(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> bytes:
    return hashlib.sha256(_canonical(value).encode("utf-8")).digest()


def _record_identity(record: dict[str, Any], fields: tuple[str, ...]) -> str | None:
    identity = {field: record[field] for field in fields if field in record}
    return _canonical(identity) if identity else None


def _record_sort_key(record: dict[str, Any], identity_fields: tuple[str, ...]) -> tuple[str, str]:
    return _record_identity(record, identity_fields) or "", _canonical(record)


def _iter_record_list_differences(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    path: str,
    identity_fields: tuple[str, ...],
) -> Iterator[Difference]:
    """Yield record-list discrepancies without collecting all of them first."""
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
        yield from _iter_value_differences(
            expected_record,
            actual_record,
            f"{path}[identity={identity_label}]",
            (path,),
        )

    for index, record in enumerate(expected_remaining):
        if index not in paired_expected:
            yield Difference(f"{path}[expected-only:{index}]", "record_missing_from_actual", record, None)
    for index, record in enumerate(actual_remaining):
        if index not in paired_actual_indices:
            yield Difference(f"{path}[actual-only:{index}]", "record_missing_from_expected", None, record)


def _iter_value_differences(
    expected: Any,
    actual: Any,
    path: str,
    path_parts: tuple[str, ...],
) -> Iterator[Difference]:
    if path_parts == ("metadata", "generated_at"):
        return

    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual), key=str):
            if key in _PRESENTATION_ONLY_RECORD_FIELDS:
                continue
            if path_parts == ("metadata",) and key in {
                "generated_at", "engine", "engine_version", *_PRESENTATION_ONLY_METADATA_FIELDS,
            }:
                continue
            child_path = f"{path}.{key}" if path else str(key)
            if key not in expected:
                yield Difference(child_path, "field_missing_from_expected", None, actual[key])
            elif key not in actual:
                yield Difference(child_path, "field_missing_from_actual", expected[key], None)
            else:
                yield from _iter_value_differences(expected[key], actual[key], child_path, (*path_parts, str(key)))
        return

    if _is_record_sequence(expected) and _is_record_sequence(actual):
        expected = expected if isinstance(expected, list) else list(expected)
        actual = actual if isinstance(actual, list) else list(actual)
        collection = path_parts[-1] if path_parts else ""
        identity_fields = UNORDERED_RECORD_KEYS.get(collection)
        if identity_fields and all(isinstance(item, dict) for item in expected) and all(
            isinstance(item, dict) for item in actual
        ):
            yield from _iter_record_list_differences(expected, actual, path, identity_fields)
            return

        if len(expected) != len(actual):
            yield Difference(path, "list_length_mismatch", len(expected), len(actual))
        for index, (left, right) in enumerate(zip(expected, actual)):
            yield from _iter_value_differences(left, right, f"{path}[{index}]", (*path_parts, str(index)))
        return

    if type(expected) is not type(actual):
        yield Difference(path, "type_mismatch", type(expected).__name__, type(actual).__name__)
    elif expected != actual:
        yield Difference(path, "value_mismatch", expected, actual)


def iter_result_differences(expected: Any, actual: Any) -> Iterator[Difference]:
    """Yield every semantic difference between row and column result data."""
    yield from _iter_value_differences(expected, actual, "", ())


def compare_result_sets(expected: Any, actual: Any) -> list[Difference]:
    """Return all differences in deterministic order for tests and tooling."""
    return sorted(
        iter_result_differences(expected, actual),
        key=lambda item: (item.path, item.kind, _canonical(item.expected), _canonical(item.actual)),
    )
