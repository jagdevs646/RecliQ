"""Compact, repeatable record collections for large reconciliation results.

The column engine can produce hundreds of thousands of matched and identity
records. Keeping every record as a separate Python dictionary multiplies the
memory needed for data that is already represented in columns. These
collections retain the same list-like read contract for reporters and result
storage while holding each field in one compact column until it is consumed.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, Callable, overload


_MISSING = object()


class ColumnarRecordRows(Sequence[dict[str, Any]]):
    """An appendable, list-like collection that stores records by field.

    Iteration and slicing return ordinary dictionaries, so existing report
    writers can keep processing bounded batches without knowing about the
    compact representation. Static fields are applied lazily; this avoids
    copying every record when the job service adds sheet-pair metadata.
    """

    def __init__(self) -> None:
        self._columns: dict[str, list[Any]] = {}
        self._length = 0
        self._static_fields: dict[str, Any] = {}

    def append(self, record: dict[str, Any]) -> None:
        new_columns = [name for name in record if name not in self._columns]
        for name in new_columns:
            self._columns[name] = [_MISSING] * self._length
        for name, values in self._columns.items():
            values.append(record[name] if name in record else _MISSING)
        self._length += 1

    def add_static_fields(self, fields: dict[str, Any]) -> None:
        """Add job-level values without expanding every stored row."""
        self._static_fields.update(fields)

    def count_field_values(self, field: str, values: Iterable[Any]) -> dict[Any, int]:
        """Count selected values without rebuilding every record dictionary."""
        requested = tuple(values)
        column = self._columns.get(field)
        if column is None:
            static_value = self._static_fields.get(field, _MISSING)
            return {
                value: self._length if static_value is not _MISSING and static_value == value else 0
                for value in requested
            }
        counts = Counter(value for value in column if value is not _MISSING)
        return {value: int(counts.get(value, 0)) for value in requested}

    def count_populated_field(self, field: str) -> int:
        """Count truthy field values without materializing rows."""
        column = self._columns.get(field)
        if column is None:
            return self._length if self._static_fields.get(field) else 0
        return sum(value is not _MISSING and bool(value) for value in column)

    def __len__(self) -> int:
        return self._length

    def _record_at(self, index: int) -> dict[str, Any]:
        if index < 0:
            index += self._length
        if index < 0 or index >= self._length:
            raise IndexError("record index out of range")
        record = dict(self._static_fields)
        for name, values in self._columns.items():
            value = values[index]
            if value is not _MISSING:
                record[name] = value
        return record

    @overload
    def __getitem__(self, index: int) -> dict[str, Any]: ...

    @overload
    def __getitem__(self, index: slice) -> list[dict[str, Any]]: ...

    def __getitem__(self, index: int | slice) -> dict[str, Any] | list[dict[str, Any]]:
        if isinstance(index, slice):
            return [self._record_at(position) for position in range(*index.indices(self._length))]
        return self._record_at(index)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for index in range(self._length):
            yield self._record_at(index)

    def to_records(self) -> list[dict[str, Any]]:
        """Materialize only for legacy callers that require JSON lists."""
        return list(self)

    def __deepcopy__(self, memo: dict[int, Any]) -> "ColumnarRecordRows":
        # Records are complete before a job service merges them. Sharing their
        # immutable columns avoids recreating a large result during that merge.
        memo[id(self)] = self
        return self


class GeneratedRecordRows(Sequence[dict[str, Any]]):
    """A repeatable result view that creates records only when consumed.

    Some column-engine paths can describe a very large set of exact matches
    with row-position arrays and a small record factory. Keeping that data
    lazy prevents those matches from being rebuilt as Python dictionaries
    before the report store asks for them in bounded batches.
    """

    def __init__(
        self,
        length: int,
        record_at: Callable[[int], dict[str, Any]],
        *,
        field_counts: dict[str, dict[Any, int]] | None = None,
        populated_counts: dict[str, int] | None = None,
    ) -> None:
        self._length = length
        self._record_at_factory = record_at
        self._field_counts = field_counts or {}
        self._populated_counts = populated_counts or {}
        self._static_fields: dict[str, Any] = {}

    def add_static_fields(self, fields: dict[str, Any]) -> None:
        self._static_fields.update(fields)

    def __len__(self) -> int:
        return self._length

    def _record_at(self, index: int) -> dict[str, Any]:
        if index < 0:
            index += self._length
        if index < 0 or index >= self._length:
            raise IndexError("record index out of range")
        record = self._record_at_factory(index)
        if self._static_fields:
            return {**record, **self._static_fields}
        return record

    @overload
    def __getitem__(self, index: int) -> dict[str, Any]: ...

    @overload
    def __getitem__(self, index: slice) -> list[dict[str, Any]]: ...

    def __getitem__(self, index: int | slice) -> dict[str, Any] | list[dict[str, Any]]:
        if isinstance(index, slice):
            return [self._record_at(position) for position in range(*index.indices(self._length))]
        return self._record_at(index)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for index in range(self._length):
            yield self._record_at(index)

    def count_field_values(self, field: str, values: Iterable[Any]) -> dict[Any, int]:
        requested = tuple(values)
        counts = self._field_counts.get(field)
        if counts is not None:
            return {value: int(counts.get(value, 0)) for value in requested}
        static_value = self._static_fields.get(field, _MISSING)
        if static_value is not _MISSING:
            return {value: self._length if static_value == value else 0 for value in requested}
        return _count_field_values_from_records(self, field, requested)

    def count_populated_field(self, field: str) -> int:
        if field in self._populated_counts:
            return self._populated_counts[field]
        if field in self._static_fields:
            return self._length if self._static_fields[field] else 0
        return sum(bool(record.get(field)) for record in self)

    def to_records(self) -> list[dict[str, Any]]:
        return list(self)

    def __deepcopy__(self, memo: dict[int, Any]) -> "GeneratedRecordRows":
        memo[id(self)] = self
        return self


class ConcatenatedRecordRows(Sequence[dict[str, Any]]):
    """A list-like view over several result collections without copying rows."""

    def __init__(self, collections: Iterable[Sequence[dict[str, Any]]]) -> None:
        self._collections = tuple(collection for collection in collections if len(collection))

    def __len__(self) -> int:
        return sum(len(collection) for collection in self._collections)

    @overload
    def __getitem__(self, index: int) -> dict[str, Any]: ...

    @overload
    def __getitem__(self, index: slice) -> list[dict[str, Any]]: ...

    def __getitem__(self, index: int | slice) -> dict[str, Any] | list[dict[str, Any]]:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step != 1:
                return [self[position] for position in range(start, stop, step)]
            records: list[dict[str, Any]] = []
            offset = 0
            for collection in self._collections:
                collection_start = max(start - offset, 0)
                collection_stop = min(stop - offset, len(collection))
                if collection_start < collection_stop:
                    records.extend(collection[collection_start:collection_stop])
                offset += len(collection)
                if offset >= stop:
                    break
            return records
        if index < 0:
            index += len(self)
        if index < 0:
            raise IndexError("record index out of range")
        for collection in self._collections:
            if index < len(collection):
                return collection[index]
            index -= len(collection)
        raise IndexError("record index out of range")

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for collection in self._collections:
            yield from collection

    def add_static_fields(self, fields: dict[str, Any]) -> None:
        for collection in self._collections:
            add_static_fields(collection, fields)

    def count_field_values(self, field: str, values: Iterable[Any]) -> dict[Any, int]:
        requested = tuple(values)
        counts = {value: 0 for value in requested}
        for collection in self._collections:
            child_counts = count_field_values(collection, field, requested)
            for value, count in child_counts.items():
                counts[value] += count
        return counts

    def count_populated_field(self, field: str) -> int:
        return sum(count_populated_field(collection, field) for collection in self._collections)

    def to_records(self) -> list[dict[str, Any]]:
        return list(self)

    def __deepcopy__(self, memo: dict[int, Any]) -> "ConcatenatedRecordRows":
        memo[id(self)] = self
        return self


def add_static_fields(records: Sequence[dict[str, Any]], fields: dict[str, Any]) -> None:
    """Apply sheet-rule scope metadata to ordinary or compact row collections."""
    if isinstance(records, (ColumnarRecordRows, GeneratedRecordRows, ConcatenatedRecordRows)):
        records.add_static_fields(fields)
        return
    for record in records:
        record.update(fields)


def count_field_values(
    records: Sequence[dict[str, Any]],
    field: str,
    values: Iterable[Any],
) -> dict[Any, int]:
    """Count selected values once for ordinary, compact, or joined results."""
    requested = tuple(values)
    if isinstance(records, (ColumnarRecordRows, GeneratedRecordRows, ConcatenatedRecordRows)):
        return records.count_field_values(field, requested)
    return _count_field_values_from_records(records, field, requested)


def count_populated_field(records: Sequence[dict[str, Any]], field: str) -> int:
    """Count truthy field values without constructing temporary result lists."""
    if isinstance(records, (ColumnarRecordRows, GeneratedRecordRows, ConcatenatedRecordRows)):
        return records.count_populated_field(field)
    return sum(bool(record.get(field)) for record in records)


def _count_field_values_from_records(
    records: Iterable[dict[str, Any]],
    field: str,
    values: tuple[Any, ...],
) -> dict[Any, int]:
    counts = {value: 0 for value in values}
    requested_values = set(values)
    for record in records:
        value = record.get(field)
        if value in requested_values:
            counts[value] += 1
    return counts


def concatenate_record_rows(
    left: Sequence[dict[str, Any]],
    right: Sequence[dict[str, Any]],
) -> Sequence[dict[str, Any]]:
    """Join result categories without materializing compact collections."""
    if not left:
        return right
    if not right:
        return left
    if isinstance(left, list) and isinstance(right, list):
        return [*left, *right]
    if isinstance(left, ConcatenatedRecordRows):
        return ConcatenatedRecordRows((*left._collections, right))
    return ConcatenatedRecordRows((left, right))
