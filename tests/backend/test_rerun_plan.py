"""Changing a finished run's rules and running it again on the same files."""
from fastapi.testclient import TestClient

from _helpers import single_rule_plan, upload, wait_for_job
from app.main import app

KEYED = {
    "matching_strategy": {"primary_key_source": ["Invoice"], "primary_key_destination": ["Invoice"]},
    "reconciliation_mapping": [{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}],
}


def test_a_finished_run_can_be_reopened_changed_and_run_again():
    with TestClient(app) as client:
        source = upload(client, "books.xlsx", {"S": [{"Invoice": "A", "Amount": 100.0}, {"Invoice": "B", "Amount": 50.0}]})
        destination = upload(client, "bank.xlsx", {"D": [{"Invoice": "A", "Amount": 103.0}, {"Invoice": "B", "Amount": 50.0}]})
        first = wait_for_job(client, client.post("/api/reconciliation/generic", json=single_rule_plan(source, destination, "S", "D", **KEYED)).json()["id"])

        setup = client.get(f"/api/jobs/{first['id']}/plan")
        assert setup.status_code == 200
        body = setup.json()
        assert [file["original_filename"] for file in body["files"]] == ["books.xlsx", "bank.xlsx"]
        rule = body["file_pairs"][0]["sheet_rules"][0]
        assert rule["source_sheets"] == ["S"] and rule["matching_strategy"]["primary_key_source"] == ["Invoice"]
        assert rule["reconciliation_mapping"] == KEYED["reconciliation_mapping"]

        # The same plan with a tolerance added runs as a new job; the first stays.
        rule["tolerances"] = [{"field": "AMOUNT", "amount": 5}]
        second = wait_for_job(client, client.post("/api/reconciliation/generic", json={"orientation": body["orientation"], "file_pairs": body["file_pairs"]}).json()["id"])
        assert second["id"] != first["id"] and second["status"] == "completed"
        assert {job["id"] for job in client.get("/api/jobs").json()["jobs"]} >= {first["id"], second["id"]}
        first_summary = client.get(f"/api/reports/job/{first['id']}/summary").json()
        second_summary = client.get(f"/api/reports/job/{second['id']}/summary").json()
        assert second_summary["within_tolerance_records"] == 1
        assert first_summary.get("within_tolerance_records", 0) == 0

        # Deleting the first run keeps the files the second one still uses.
        assert client.delete(f"/api/jobs/{first['id']}").status_code == 204
        assert client.get(f"/api/jobs/{second['id']}/plan").status_code == 200


def test_reopening_an_unknown_run_is_not_found():
    with TestClient(app) as client:
        assert client.get("/api/jobs/no-such-job/plan").status_code == 404
