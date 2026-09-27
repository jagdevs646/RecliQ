"""Data-quality pre-check, saved reconciliations, and alias learning."""
from fastapi.testclient import TestClient

from _helpers import preview, single_rule_plan, upload, wait_for_job
from app.main import app

KEYED = {
    "matching_strategy": {"primary_key_source": ["Invoice"], "primary_key_destination": ["Invoice"]},
    "reconciliation_mapping": [{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}],
}


def _issues(result: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for rule in result["rules"]:
        for issue in rule["issues"]:
            grouped.setdefault(issue["category"], []).append(issue)
    return grouped


# ── Pre-check ──────────────────────────────────────────────────────────────
def test_precheck_reports_input_problems_before_matching():
    with TestClient(app) as client:
        source = upload(client, "books.xlsx", {"S": [
            {"Invoice": "INV-1", "Amount": 10, "Date": "03/25/2026"},
            {"Invoice": "INV-1", "Amount": 5, "Date": "03/26/2026"},
            {"Invoice": None, "Amount": 7, "Date": "04/01/2026"},
            {"Invoice": "INV-3", "Amount": "TBD", "Date": "04/02/2026"},
        ]})
        destination = upload(client, "bank.xlsx", {"D": [
            {"Invoice": "INV/1", "Amount": 15, "Date": "2026-03-25"},
            {"Invoice": "INV/9", "Amount": 3, "Date": "2026-04-02"},
        ]})
        plan = single_rule_plan(source, destination, "S", "D", **{
            **KEYED,
            "reconciliation_mapping": KEYED["reconciliation_mapping"] + [{"file_1_fields": ["Date"], "file_2_fields": ["Date"]}],
        })
        result = client.post("/api/analysis/precheck", json=plan).json()

    issues = _issues(result)
    assert result["status"] == "blockers"
    assert issues["blank_keys"][0]["count"] == 1 and issues["blank_keys"][0]["side"] == "source"
    assert issues["duplicate_keys"][0]["count"] == 1  # INV-1 twice: combined.
    assert any(issue["count"] == 1 for issue in issues["non_numeric"])  # "TBD"
    date_blocker = next(issue for issue in issues["date_format"] if issue["severity"] == "blocker")
    assert date_blocker["suggestion"] == {"date_format": "month_first"}
    overlap = issues["key_overlap"][0]
    assert overlap["count"] == 1  # Only INV-1 exists on both sides.


def test_precheck_blocks_missing_columns_and_passes_clean_data():
    with TestClient(app) as client:
        source = upload(client, "a.xlsx", {"S": [{"Invoice": "A", "Amount": 1}]})
        destination = upload(client, "b.xlsx", {"D": [{"Invoice": "A", "Amount": 1}]})
        clean = client.post("/api/analysis/precheck", json=single_rule_plan(source, destination, "S", "D", **KEYED)).json()
        broken = client.post("/api/analysis/precheck", json=single_rule_plan(source, destination, "S", "D", **{
            **KEYED, "reconciliation_mapping": [{"file_1_fields": ["Amount"], "file_2_fields": ["Net Amount"]}],
        })).json()
    assert clean["status"] == "ok"
    assert broken["status"] == "blockers" and "NET AMOUNT" in _issues(broken)["missing_column"][0]["message"]


def test_acknowledged_precheck_is_stored_with_the_job_and_audited():
    with TestClient(app) as client:
        source = upload(client, "a.xlsx", {"S": [{"Invoice": "A", "Amount": 1}]})
        destination = upload(client, "b.xlsx", {"D": [{"Invoice": "A", "Amount": 1}]})
        plan = {**single_rule_plan(source, destination, "S", "D", **KEYED), "precheck_acknowledged": True,
                "precheck_summary": {"status": "warnings", "warning": 2}}
        job = wait_for_job(client, client.post("/api/reconciliation/generic", json=plan).json()["id"])
        created = next(event for event in client.get("/api/audit/events").json()["events"] if event["action"] == "job.created")
    assert job["status"] == "completed"
    assert created["metadata"]["precheck_acknowledged"] is True
    assert created["metadata"]["precheck_summary"]["warning"] == 2


# ── Saved reconciliations ──────────────────────────────────────────────────
def test_saved_reconciliation_reruns_on_new_files_with_renamed_columns():
    with TestClient(app) as client:
        source = upload(client, "Sept books.xlsx", {"Ledger": [{"Invoice No": "INV-1", "Vendor": "ABC Pvt Ltd", "Amount": 10}]})
        destination = upload(client, "Sept bank.xlsx", {"Bank": [{"Invoice No": "INV/1", "Vendor": "ABC Private Limited", "Amount": 10}]})
        plan = single_rule_plan(source, destination, "Ledger", "Bank", **{
            "matching_strategy": {
                "primary_key_source": ["Invoice No"], "primary_key_destination": ["Invoice No"],
                "secondary_conditions": [{"source_column": "Vendor", "destination_column": "Vendor", "comparison_method": "exact_text"}],
            },
            "reconciliation_mapping": [{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}],
            "transformations": [{"operation": "trim", "side": "both", "columns": ["Vendor"]}],
            "date_format": "month_first",
        })
        created = client.post("/api/templates", json={"name": "Monthly bank rec", "plan": plan, "report_settings": {"include_matched": False}})
        assert created.status_code == 201, created.text
        template = created.json()
        assert template["current_version"] == 1 and template["summary"]["sheet_rules"] == 1
        rule = template["config"]["file_pairs"][0]["sheet_rules"][0]
        assert rule["date_format"] == "month_first" and rule["transformations"][0]["operation"] == "trim"

        # October files: renamed sheet and columns.
        new_source = upload(client, "Oct books.xlsx", {"Ledger Oct": [{"Invoice_No": "INV-7", "Vendor Name": "M/s XYZ Co", "Value Rs": 20}]})
        new_destination = upload(client, "Oct bank.xlsx", {"Bank": [{"Invoice Number": "INV/7", "Vendor": "XYZ Company", "Amount": 20}]})
        files = [{"file_pair_id": "pair-1", "source_file_id": new_source, "destination_file_id": new_destination}]

        unresolved = client.post(f"/api/templates/{template['id']}/resolve", json={"files": files})
        assert unresolved.status_code == 422
        problems = unresolved.json()["resolution"]["unresolved"]
        assert {problem.get("column") for problem in problems} == {"AMOUNT"}  # Never guessed.

        overrides = {"column_overrides": {"rule-1-1": {"source": {"Amount": "Value Rs"}}}}
        resolved = client.post(f"/api/templates/{template['id']}/resolve", json={"files": files, **overrides})
        assert resolved.status_code == 200, resolved.text
        methods = {(item["template"], item["method"]) for item in resolved.json()["resolution"]["automatic"]}
        assert ("INVOICE NO", "same name, different punctuation") in methods
        assert ("VENDOR", "accounting synonym") in methods  # Vendor -> Vendor Name.
        assert ("AMOUNT", "chosen by you") in methods
        assert ("Ledger", "name contains the saved name") in methods  # Sheet "Ledger Oct".

        job = client.post(f"/api/templates/{template['id']}/run", json={"files": files, **overrides})
        assert job.status_code == 200, job.text
        finished = wait_for_job(client, job.json()["id"])
        assert finished["status"] == "completed" and finished["template_id"] == template["id"] and finished["template_version"] == 1
        summary = client.get(f"/api/reports/job/{finished['id']}/summary").json()
        assert summary["exact_matches"] == 1  # "M/s XYZ Co" = "XYZ Company".

        # Editing creates a new version; old versions stay readable.
        updated = client.put(f"/api/templates/{template['id']}", json={"column_aliases": {"AMOUNT": ["Value Rs"]}, "change_note": "Oct names"})
        assert updated.json()["current_version"] == 2
        assert client.get(f"/api/templates/{template['id']}/versions/1").json()["config"]["column_aliases"] == {}
        # With the saved alternative name, no override is needed any more.
        assert client.post(f"/api/templates/{template['id']}/resolve", json={"files": files}).status_code == 200

        assert client.delete(f"/api/templates/{template['id']}").json()["archived"] is True
        assert client.get("/api/templates").json()["templates"] == []
        actions = {event["action"] for event in client.get("/api/audit/events").json()["events"]}
    assert {"template.created", "template.run", "template.updated", "template.archived"} <= actions


def test_template_can_be_saved_from_a_finished_job():
    with TestClient(app) as client:
        source = upload(client, "a.xlsx", {"S": [{"Invoice": "A", "Amount": 1}]})
        destination = upload(client, "b.xlsx", {"D": [{"Invoice": "A", "Amount": 1}]})
        job = wait_for_job(client, client.post("/api/reconciliation/generic", json=single_rule_plan(source, destination, "S", "D", **KEYED)).json()["id"])
        saved = client.post("/api/templates", json={"name": "From job", "job_id": job["id"]}).json()
    pair = saved["config"]["file_pairs"][0]
    assert pair["source_filename"] == "a.xlsx" and pair["sheet_rules"][0]["source_sheets"] == ["S"]


# ── Aliases learned from decisions (never automatically) ──────────────────
def test_alias_suggestions_need_repeated_acceptance_and_explicit_approval():
    with TestClient(app) as client:
        decision = {"value_1": "IBM", "value_2": "International Business Machines", "decision": "accept", "column_hint": "VENDOR"}
        client.post("/api/aliases/decisions", json={**decision, "job_id": "job-1"})
        assert client.get("/api/aliases/suggestions").json()["suggestions"] == []  # One job is not enough.
        client.post("/api/aliases/decisions", json={**decision, "job_id": "job-2"})
        suggestions = client.get("/api/aliases/suggestions").json()["suggestions"]
        assert len(suggestions) == 1 and suggestions[0]["jobs"] == 2
        assert client.get("/api/aliases").json()["aliases"] == []  # Still not active.

        approved = client.post("/api/aliases/suggestions/approve", json={"canonical": "International Business Machines", "variant": "IBM"})
        assert approved.status_code == 201
        preview_body = client.post("/api/aliases/preview", json={"value_1": "IBM", "value_2": "International Business Machines"}).json()
        assert preview_body["equivalent"] is True

        # The alias now applies to this organization's runs.
        source = upload(client, "a.xlsx", {"S": [{"Invoice": "A", "Vendor": "IBM", "Amount": 1}]})
        destination = upload(client, "b.xlsx", {"D": [{"Invoice": "A", "Vendor": "International Business Machines", "Amount": 1}]})
        plan = single_rule_plan(source, destination, "S", "D", **{
            **KEYED,
            "matching_strategy": {**KEYED["matching_strategy"], "secondary_conditions": [
                {"source_column": "Vendor", "destination_column": "Vendor", "comparison_method": "exact_text"}]},
        })
        job = wait_for_job(client, client.post("/api/reconciliation/generic", json=plan).json()["id"])
        matched = preview(client, job["id"], "review") == [] and client.get(f"/api/reports/job/{job['id']}/summary").json()
    assert matched["exact_matches"] == 1


def test_a_single_rejection_blocks_a_suggestion_and_conflicts_are_refused():
    with TestClient(app) as client:
        pair = {"value_1": "Apex Ltd", "value_2": "Apex Holdings", "column_hint": "VENDOR"}
        for job_id in ("j1", "j2", "j3"):
            client.post("/api/aliases/decisions", json={**pair, "decision": "accept", "job_id": job_id})
        client.post("/api/aliases/decisions", json={**pair, "decision": "reject", "job_id": "j4"})
        assert client.get("/api/aliases/suggestions").json()["suggestions"] == []

        assert client.post("/api/aliases", json={"canonical": "Globex", "variants": ["GBX"]}).status_code == 201
        conflict = client.post("/api/aliases", json={"canonical": "Initech", "variants": ["GBX"]})
        assert conflict.status_code == 409
        redundant = client.post("/api/aliases", json={"canonical": "ABC Pvt Ltd", "variants": ["ABC Private Limited"]})
        assert redundant.status_code == 422  # Built-in normalization already covers it.


def test_column_resolution_is_certain_or_asks():
    from app.services.template_service import resolve_name

    assert resolve_name("INVOICE", ["INVOICE_NO", "PARTY NAME", "TXN DATE"]) == ("INVOICE_NO", "name contains the saved name")
    assert resolve_name("DATE", ["INVOICE_NO", "TXN DATE"]) == ("TXN DATE", "name contains the saved name")
    assert resolve_name("AMOUNT", ["NET AMOUNT", "GROSS AMOUNT"]) == (None, "not found")  # Two candidates: ask.
    assert resolve_name("INVOICE NO", ["Invoice_No"])[1] == "same name, different punctuation"
    assert resolve_name("AMOUNT", ["Value Rs"], ["Value Rs"]) == ("Value Rs", "saved alternative name")
