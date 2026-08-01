import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "RecliQ API"


def test_gst_configuration_is_exposed_from_the_engine_contract():
    client = TestClient(app)
    response = client.get("/api/reconciliation/gst/config")
    assert response.status_code == 200
    config = response.json()
    assert "GSTR" in config["matching_fields"]
    assert "INVOICE NO." in config["required_columns"]


def test_sample_template_endpoints():
    client = TestClient(app)
    # Generic sample template
    resp_generic = client.get("/api/reconciliation/sample-template?type=generic")
    assert resp_generic.status_code == 200
    assert "spreadsheetml" in resp_generic.headers["content-type"]
    assert "RecliQ_General_Sample_Template.xlsx" in resp_generic.headers["content-disposition"]

    # GST sample template
    resp_gst = client.get("/api/reconciliation/sample-template?type=gst")
    assert resp_gst.status_code == 200
    assert "spreadsheetml" in resp_gst.headers["content-type"]
    assert "RecliQ_GST_Sample_Template.xlsx" in resp_gst.headers["content-disposition"]
