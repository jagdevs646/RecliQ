from __future__ import annotations

import json
import sys
from pathlib import Path

PERF_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "perf"
sys.path.insert(0, str(PERF_SCRIPTS))

from result_comparison import compare_result_files, compare_result_sets


def test_record_order_and_generated_timestamp_do_not_affect_comparison() -> None:
    expected = {
        "metadata": {"generated_at": "old", "matching_keys": ["Invoice", "Entity"]},
        "matched_records": [{"ROW (FILE 1)": 2, "ROW (FILE 2)": 4, "Amount": 10},
                            {"ROW (FILE 1)": 3, "ROW (FILE 2)": 5, "Amount": 20}],
        "field_exception_summary": [{"Field": "Amount", "Mismatch": 1}, {"Field": "Date", "Mismatch": 0}],
    }
    actual = {
        "metadata": {"generated_at": "new", "matching_keys": ["Invoice", "Entity"]},
        "matched_records": list(reversed(expected["matched_records"])),
        "field_exception_summary": list(reversed(expected["field_exception_summary"])),
    }

    assert compare_result_sets(expected, actual) == []


def test_reports_field_differences_on_the_matching_record_not_list_position() -> None:
    expected = {"matched_records": [
        {"ROW (FILE 1)": 2, "ROW (FILE 2)": 4, "Amount": 10},
        {"ROW (FILE 1)": 3, "ROW (FILE 2)": 5, "Amount": 20},
    ]}
    actual = {"matched_records": [
        {"ROW (FILE 1)": 3, "ROW (FILE 2)": 5, "Amount": 20},
        {"ROW (FILE 1)": 2, "ROW (FILE 2)": 4, "Amount": 11},
    ]}

    differences = compare_result_sets(expected, actual)

    assert len(differences) == 1
    assert differences[0].kind == "value_mismatch"
    assert differences[0].path.startswith('matched_records[identity={"ROW (FILE 1)":2,"ROW (FILE 2)":4}]')
    assert differences[0].path.endswith(".Amount")
    assert differences[0].expected == 10
    assert differences[0].actual == 11


def test_reports_added_and_removed_records_as_multiset_differences() -> None:
    record = {"ROW (FILE 1)": 7, "Value": "A"}

    differences = compare_result_sets({"matched_records": [record, record]}, {"matched_records": [record]})

    assert len(differences) == 1
    assert differences[0].kind == "record_missing_from_actual"
    assert differences[0].expected == record


def test_duplicate_record_identities_are_paired_independently_of_input_order() -> None:
    expected_records = [
        {"Primary Key": "INV-1", "Field": "Amount", "File 1 Value": 100, "File 2 Value": 101},
        {"Primary Key": "INV-1", "Field": "Amount", "File 1 Value": 200, "File 2 Value": 201},
    ]
    actual_records = [
        {"Primary Key": "INV-1", "Field": "Amount", "File 1 Value": 102, "File 2 Value": 103},
        {"Primary Key": "INV-1", "Field": "Amount", "File 1 Value": 202, "File 2 Value": 203},
    ]

    forward = compare_result_sets({"exceptions": expected_records}, {"exceptions": actual_records})
    reversed_input = compare_result_sets(
        {"exceptions": list(reversed(expected_records))},
        {"exceptions": list(reversed(actual_records))},
    )

    assert forward == reversed_input
    assert len(forward) == 4


def test_preserves_order_for_configuration_lists_and_nested_lists() -> None:
    expected = {"metadata": {"matching_keys": ["Invoice", "Entity"]}}
    actual = {"metadata": {"matching_keys": ["Entity", "Invoice"]}}

    differences = compare_result_sets(expected, actual)

    assert [difference.path for difference in differences] == [
        "metadata.matching_keys[0]",
        "metadata.matching_keys[1]",
    ]


def test_streaming_file_comparison_ignores_record_order_and_generated_timestamp(tmp_path: Path) -> None:
    expected = {
        "metadata": {"generated_at": "old", "matching_keys": ["Invoice"]},
        "exceptions": [{"Primary Key": f"INV-{index}", "Field": "Amount", "Value": index} for index in range(20)],
    }
    actual = {
        "metadata": {"generated_at": "new", "matching_keys": ["Invoice"]},
        "exceptions": list(reversed(expected["exceptions"])),
    }
    expected_path = tmp_path / "expected.json"
    actual_path = tmp_path / "actual.json"
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    actual_path.write_text(json.dumps(actual), encoding="utf-8")

    assert compare_result_files(expected_path, actual_path) == (0, [])


def test_streaming_file_comparison_reports_record_multiset_difference(tmp_path: Path) -> None:
    expected_path = tmp_path / "expected.json"
    actual_path = tmp_path / "actual.json"
    expected_path.write_text('{"exceptions":[{"Primary Key":"INV-1","Value":10}]}', encoding="utf-8")
    actual_path.write_text('{"exceptions":[{"Primary Key":"INV-1","Value":11}]}', encoding="utf-8")

    count, differences = compare_result_files(expected_path, actual_path)

    assert count == 2
    assert len(differences) == 2
    assert all(item.kind == "record_count_mismatch" for item in differences)
