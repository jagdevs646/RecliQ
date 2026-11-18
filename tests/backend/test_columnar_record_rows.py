from __future__ import annotations

from app.reconciliation_engine.record_collections import (
    ColumnarRecordRows,
    GeneratedRecordRows,
    add_static_fields,
    concatenate_record_rows,
    count_field_values,
    count_populated_field,
)
from app.utils.json_encoder import safe_json_dumps


def test_columnar_record_rows_preserve_optional_and_null_fields() -> None:
    rows = ColumnarRecordRows()
    rows.append({"Key": "A", "Explicit null": None})
    rows.append({"Key": "B", "Optional": "shown"})
    add_static_fields(rows, {"File Pair ID": "pair-1"})

    assert len(rows) == 2
    assert rows[0] == {"File Pair ID": "pair-1", "Key": "A", "Explicit null": None}
    assert rows[1] == {"File Pair ID": "pair-1", "Key": "B", "Optional": "shown"}
    assert rows[:] == [rows[0], rows[1]]
    assert '"Key":"A"' in safe_json_dumps({"rows": rows}, separators=(",", ":"))


def test_compact_record_collections_merge_without_materializing_rows() -> None:
    left = ColumnarRecordRows()
    left.append({"Key": "A"})
    right = ColumnarRecordRows()
    right.append({"Key": "B"})

    merged = concatenate_record_rows(left, right)

    assert len(merged) == 2
    assert list(merged) == [{"Key": "A"}, {"Key": "B"}]


def test_compact_record_counts_do_not_materialize_records() -> None:
    records = ColumnarRecordRows()
    records.append({"Key": "A", "classification": "EXACT_MATCH", "note": "normalized"})
    records.append({"Key": "B", "classification": "EXACT_MATCH"})
    records.append({"Key": "C", "classification": "NOT_FOUND", "note": ""})

    assert count_field_values(records, "classification", ("EXACT_MATCH", "NOT_FOUND", "MISSING")) == {
        "EXACT_MATCH": 2,
        "NOT_FOUND": 1,
        "MISSING": 0,
    }
    assert count_populated_field(records, "note") == 1


def test_generated_record_rows_are_lazy_and_keep_static_metadata() -> None:
    generated = GeneratedRecordRows(
        3,
        lambda index: {"Key": index + 1, "classification": "EXACT_MATCH"},
        field_counts={"classification": {"EXACT_MATCH": 3}},
    )
    add_static_fields(generated, {"File Pair ID": "pair-1"})

    assert generated[1] == {"Key": 2, "classification": "EXACT_MATCH", "File Pair ID": "pair-1"}
    assert generated[1:] == [
        {"Key": 2, "classification": "EXACT_MATCH", "File Pair ID": "pair-1"},
        {"Key": 3, "classification": "EXACT_MATCH", "File Pair ID": "pair-1"},
    ]
    assert count_field_values(generated, "classification", ("EXACT_MATCH", "NOT_FOUND")) == {
        "EXACT_MATCH": 3,
        "NOT_FOUND": 0,
    }
