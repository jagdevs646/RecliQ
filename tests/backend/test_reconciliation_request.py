import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.reconciliation import (
    FilePairConfig,
    FileSource,
    GenericReconciliationRequest,
    GSTReconciliationRequest,
    MatchingStrategy,
    RuleMapping,
    SheetPairConfig,
    SheetRuleConfig,
    normalize_legacy_request,
)
from app.services.reconciliation_service import _merge_rule_universal_data
from app.api.routes.reports import _PREVIEW_SECTIONS, _report_preview_data


def test_schema_model_validators():
    # Only source_files supplied
    req = GenericReconciliationRequest(
        source_files_1=[FileSource(file_id="f1", sheet_id="s1")],
        source_files_2=[FileSource(file_id="f2", sheet_id="s2")],
        key_file_1="ID",
        key_file_2="ID",
        rules=[]
    )
    assert req.file_1_id == "f1"
    assert req.file_2_id == "f2"

    # Only file_1_id / file_2_id supplied
    req_legacy = GenericReconciliationRequest(
        file_1_id="f1",
        file_2_id="f2",
        key_file_1="ID",
        key_file_2="ID",
        rules=[]
    )
    assert len(req_legacy.source_files_1) == 1
    assert req_legacy.source_files_1[0].file_id == "f1"
    assert len(req_legacy.source_files_2) == 1
    assert req_legacy.source_files_2[0].file_id == "f2"

    # GST request validator
    gst_req = GSTReconciliationRequest(
        source_files_1=[FileSource(file_id="g1")],
        source_files_2=[FileSource(file_id="g2")]
    )
    assert gst_req.file_1_id == "g1"
    assert gst_req.file_2_id == "g2"


def test_legacy_generic_request_normalizes_to_one_canonical_plan():
    request = GenericReconciliationRequest(
        source_files_1=[FileSource(file_id="source-file", sheet_id="Sales")],
        source_files_2=[FileSource(file_id="destination-file", sheet_id="Ledger")],
        key_file_1=["Invoice", "Entity"],
        key_file_2=["Invoice Number", "Company Code"],
        rules=[RuleMapping(file_1_fields=["Amount"], file_2_fields=["Net Amount"])],
        include_columns_file_1=["Currency"],
        include_columns_file_2=["Tax"],
    )

    plan = normalize_legacy_request(request)

    assert len(plan.file_pairs) == 1
    rule = plan.file_pairs[0].sheet_rules[0]
    assert rule.matching_strategy.primary_key_source == ["Invoice", "Entity"]
    assert rule.matching_strategy.primary_key_destination == ["Invoice Number", "Company Code"]
    assert rule.reconciliation_mapping == request.rules
    assert rule.source_sheets == ["Sales"]
    assert rule.destination_sheets == ["Ledger"]
    assert plan.execution_pairs()[0]["key_file_1"] == ["Invoice", "Entity"]


def test_legacy_sheet_pairs_keep_independent_rule_configuration():
    request = GenericReconciliationRequest(
        pairs=[
            SheetPairConfig(
                source_file_1=FileSource(file_id="source-file", sheet_id="Sales"),
                source_file_2=FileSource(file_id="destination-file", sheet_id="Sales Ledger"),
                key_file_1="Invoice",
                key_file_2="Invoice Number",
                rules=[RuleMapping(file_1_fields=["Amount"], file_2_fields=["Net Amount"])],
            ),
            SheetPairConfig(
                source_file_1=FileSource(file_id="source-file", sheet_id="Payments"),
                source_file_2=FileSource(file_id="destination-file", sheet_id="Bank"),
                matching_strategy=MatchingStrategy(
                    primary_key_source=["Reference", "Date"],
                    primary_key_destination=["Bank Ref", "Posting Date"],
                ),
                reconciliation_mapping=[RuleMapping(file_1_fields=["Paid"], file_2_fields=["Credit"])],
            ),
        ]
    )

    plan = normalize_legacy_request(request)
    first_rule = plan.file_pairs[0].sheet_rules[0]
    second_rule = plan.file_pairs[1].sheet_rules[0]

    assert first_rule.matching_strategy.primary_key_source == ["Invoice"]
    assert second_rule.matching_strategy.primary_key_source == ["Reference", "Date"]
    assert first_rule.reconciliation_mapping[0].file_1_fields == ["Amount"]
    assert second_rule.reconciliation_mapping[0].file_1_fields == ["Paid"]
    assert plan.execution_pairs()[1]["source_file_1"]["sheet_id"] == "Payments"


def test_canonical_file_pairs_are_accepted_without_legacy_fields():
    file_pair = FilePairConfig(
        file_pair_id="january",
        source_files=[FileSource(file_id="source-file")],
        destination_files=[FileSource(file_id="destination-file")],
        sheet_rules=[
            SheetRuleConfig(
                sheet_rule_id="january-sales",
                source_sheets=["Sales"],
                destination_sheets=["Ledger"],
                matching_strategy=MatchingStrategy(
                    primary_key_source=["Invoice"],
                    primary_key_destination=["Invoice Number"],
                ),
                reconciliation_mapping=[RuleMapping(file_1_fields=["Amount"], file_2_fields=["Amount"])],
                report_label="January sales",
            )
        ],
    )
    request = GenericReconciliationRequest(file_pairs=[file_pair])

    plan = normalize_legacy_request(request)

    assert plan.file_pairs[0].file_pair_id == "january"
    assert plan.execution_pairs()[0]["report_label"] == "January sales"


def test_execution_rules_keep_each_file_pair_and_sheet_rule_independent():
    plan = normalize_legacy_request(
        {
            "file_pairs": [
                {
                    "file_pair_id": "north",
                    "source_files": [{"file_id": "source", "sheet_id": "North"}],
                    "destination_files": [{"file_id": "destination", "sheet_id": "North ledger"}],
                    "sheet_rules": [
                        {
                            "sheet_rule_id": "north-rule",
                            "source_sheets": ["North"],
                            "destination_sheets": ["North ledger"],
                            "matching_strategy": {
                                "primary_key_source": ["Invoice"],
                                "primary_key_destination": ["Document"],
                            },
                            "reconciliation_mapping": [{"file_1_fields": ["Amount"], "file_2_fields": ["Net"]}],
                            "report_label": "North reconciliation",
                        }
                    ],
                },
                {
                    "file_pair_id": "south",
                    "source_files": [{"file_id": "source", "sheet_id": "South"}],
                    "destination_files": [{"file_id": "destination", "sheet_id": "South ledger"}],
                    "sheet_rules": [
                        {
                            "sheet_rule_id": "south-rule",
                            "source_sheets": ["South"],
                            "destination_sheets": ["South ledger"],
                            "matching_strategy": {
                                "primary_key_source": ["Reference", "Entity"],
                                "primary_key_destination": ["Ref", "Company"],
                            },
                            "reconciliation_mapping": [{"file_1_fields": ["Paid"], "file_2_fields": ["Credit"]}],
                            "report_label": "South reconciliation",
                        }
                    ],
                },
            ]
        }
    )

    rules = plan.execution_rules()
    assert [rule["file_pair_id"] for rule in rules] == ["north", "south"]
    assert rules[0]["key_file_1"] == ["Invoice"]
    assert rules[1]["key_file_1"] == ["Reference", "Entity"]
    assert rules[0]["rules"][0]["file_1_fields"] == ["Amount"]
    assert rules[1]["rules"][0]["file_1_fields"] == ["Paid"]


def test_rule_result_merge_preserves_nested_identity_statistics():
    base = {
        "statistics": {"matched": 1, "identity": {"EXACT_MATCH": 1}},
        "overall_status": "PASSED",
        "control_checks": [{"File 1": 1, "File 2": 1, "Result": "Pass"}],
        "exceptions": [], "matched_records": [], "missing_in_file_1": [], "missing_in_file_2": [],
        "field_differences": [], "identity_resolution": [],
    }
    second = {
        "statistics": {"matched": 2, "identity": {"EXCEPTION_MATCH": 2}},
        "overall_status": "EXCEPTIONS FOUND",
        "control_checks": [{"File 1": 2, "File 2": 2, "Result": "Exception"}],
        "exceptions": [], "matched_records": [], "missing_in_file_1": [], "missing_in_file_2": [],
        "field_differences": [], "identity_resolution": [],
    }

    merged = _merge_rule_universal_data([
        {"file_pair_id": "north", "sheet_rule_id": "north-rule", "universal_data": base},
        {"file_pair_id": "south", "sheet_rule_id": "south-rule", "universal_data": second},
    ])

    assert merged["statistics"]["matched"] == 3
    assert merged["statistics"]["identity"] == {"EXACT_MATCH": 1, "EXCEPTION_MATCH": 2}
    assert merged["control_checks"][0]["File 1"] == 3
    assert len(merged["execution_results"]) == 2


def test_preview_metadata_uses_stored_sections_instead_of_workbook_sheet_names(tmp_path: Path):
    report_path = tmp_path / "Reconciliation.xlsx"
    raw_path = tmp_path / "Reconciliation_data.json"
    raw_path.write_text(json.dumps({"identity_resolution": [{"IDENTITY CLASSIFICATION": "AMBIGUOUS_MATCH"}]}), encoding="utf-8")

    assert _PREVIEW_SECTIONS["review"] == "identity_resolution"
    assert _report_preview_data(report_path)["identity_resolution"][0]["IDENTITY CLASSIFICATION"] == "AMBIGUOUS_MATCH"


def test_enqueue_with_source_files_only():
    with TestClient(app) as client:
        # Create dummy excel files
        df1 = pd.DataFrame({"ID": ["A1", "A2"], "Amount": [100, 200]})
        df2 = pd.DataFrame({"ID": ["A1", "A2"], "Amount": [100, 200]})

        buf1 = io.BytesIO()
        df1.to_excel(buf1, index=False)
        buf1.seek(0)

        buf2 = io.BytesIO()
        df2.to_excel(buf2, index=False)
        buf2.seek(0)

        res1 = client.post("/api/files/upload", files={"file": ("file1.xlsx", buf1, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        res2 = client.post("/api/files/upload", files={"file": ("file2.xlsx", buf2, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})

        assert res1.status_code == 201
        assert res2.status_code == 201

        f1_id = res1.json()["id"]
        f2_id = res2.json()["id"]

        # Call /api/reconciliation/generic with ONLY source_files_1 and source_files_2 (no file_1_id or file_2_id)
        payload = {
            "source_files_1": [{"file_id": f1_id, "sheet_id": "Sheet1"}],
            "source_files_2": [{"file_id": f2_id, "sheet_id": "Sheet1"}],
            "key_file_1": "ID",
            "key_file_2": "ID",
            "rules": [{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}],
            "orientation": "vertical",
            "include_columns_file_1": [],
            "include_columns_file_2": []
        }

        res_recon = client.post("/api/reconciliation/generic", json=payload)
        assert res_recon.status_code == 200
        job_data = res_recon.json()
        assert job_data["status"] in ("queued", "processing", "completed")
        assert job_data["input_file_1_id"] == f1_id
        assert job_data["input_file_2_id"] == f2_id
