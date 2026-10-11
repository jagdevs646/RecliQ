from __future__ import annotations

import json
import pyotp
from fastapi.testclient import TestClient

from app.database.session import SessionLocal
from app.models.account import AdminAuditEvent, User
from app.models.audit import AuditEvent
from app.models.support import SupportTicket
from app.services.account_security import hash_password


def create_user(*, username: str, role: str = "user", mfa: bool = False) -> User:
    db = SessionLocal()
    try:
        secret = pyotp.random_base32() if mfa else None
        user = User(
            username=username,
            username_key=username.casefold(),
            email=f"{username}@example.test",
            email_key=f"{username}@example.test",
            display_name=f"{username.title()} User",
            password_hash=hash_password("support-test-password-8831"),
            role=role,
            status="active",
            email_verified=True,
            mfa_enabled=mfa,
            mfa_secret=secret,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def login(client: TestClient, user: User) -> None:
    payload = {"identifier": user.username, "password": "support-test-password-8831"}
    if user.mfa_enabled:
        payload["totp_code"] = pyotp.TOTP(user.mfa_secret).now()
    response = client.post("/api/auth/login", json=payload)
    assert response.status_code == 200, response.text


def csrf_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/auth/me").headers["x-csrf-token"]
    return {"X-CSRF-Token": token}


def ticket_payload(**changes) -> dict:
    return {
        "category": "matching_and_mapping",
        "custom_area": "",
        "workflow_stage": "column_mapping",
        "impact": "unable_to_complete",
        "subject": "Multiple columns are not mapping",
        "description": "Selecting three amount columns prevents me from continuing.",
        "expected_behavior": "I should be able to map all three columns to one total.",
        "reproduction_steps": "Open Map Columns, select three fields, then continue.",
        **changes,
    }


def test_guest_support_requests_are_isolated_and_admin_can_triage(client: TestClient):
    guest_scope = client.get("/api/session").json()["session_id"]
    created = client.post("/api/support", json=ticket_payload())
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    assert created.json()["requester_type"] == "guest"
    assert created.json()["status"] == "new"
    assert created.json()["requester"]["scope_id"] == guest_scope

    with TestClient(client.app) as another_guest:
        assert another_guest.get("/api/support").json()["total"] == 0
        assert another_guest.get(f"/api/support/{ticket_id}").status_code == 404
        assert another_guest.post(f"/api/support/{ticket_id}/messages", json={"body": "Can I see this?"}).status_code == 404

    assert client.get("/api/admin/complaints/summary").status_code == 401
    normal_user = create_user(username="supportmember")
    login(client, normal_user)
    assert client.get("/api/admin/complaints").status_code == 403
    client.post("/api/auth/logout", headers=csrf_headers(client))

    admin = create_user(username="supportadmin", role="super_admin", mfa=True)
    login(client, admin)
    summary = client.get("/api/admin/complaints/summary").json()
    assert summary["total"] == 1
    assert summary["unread"] == 1
    assert summary["by_category"] == {"matching_and_mapping": 1}

    listing = client.get("/api/admin/complaints?q=multiple&page=1&page_size=25")
    assert listing.status_code == 200, listing.text
    assert listing.json()["tickets"][0]["requester"]["display_name"] == "Guest workspace"

    detail = client.get(f"/api/admin/complaints/{ticket_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["description"] == ticket_payload()["description"]
    assert client.get("/api/admin/complaints/summary").json()["unread"] == 0

    updated = client.patch(f"/api/admin/complaints/{ticket_id}", headers=csrf_headers(client), json={
        "status": "in_progress", "priority": "high", "reply": "We are checking the mapping behavior."
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "in_progress"
    assert updated.json()["priority"] == "high"
    assert updated.json()["messages"][-1]["author_type"] == "admin"

    db = SessionLocal()
    try:
        ticket = db.get(SupportTicket, ticket_id)
        assert ticket is not None and ticket.scope_id == guest_scope
        events = db.query(AuditEvent).filter(AuditEvent.scope_id == guest_scope, AuditEvent.entity_type == "support_ticket").all()
        assert {event.action for event in events} >= {"support.ticket_created", "support.complaint_updated"}
        assert all("We are checking" not in (event.after_json or "") for event in events)
    finally:
        db.close()


def test_registered_user_can_only_read_own_support_and_reply_reopens_resolved(client: TestClient):
    owner = create_user(username="ticketowner")
    login(client, owner)
    response = client.post("/api/support", headers=csrf_headers(client), json=ticket_payload())
    assert response.status_code == 201, response.text
    ticket_id = response.json()["id"]

    client.post("/api/auth/logout", headers=csrf_headers(client))
    unrelated = create_user(username="unrelated")
    login(client, unrelated)
    assert client.get("/api/support").json()["total"] == 0
    assert client.get(f"/api/support/{ticket_id}").status_code == 404

    client.post("/api/auth/logout", headers=csrf_headers(client))
    login(client, owner)
    admin = create_user(username="resolveadmin", role="super_admin", mfa=True)
    client.post("/api/auth/logout", headers=csrf_headers(client))
    login(client, admin)
    resolved = client.patch(f"/api/admin/complaints/{ticket_id}", headers=csrf_headers(client), json={"status": "resolved"})
    assert resolved.status_code == 200, resolved.text
    client.post("/api/auth/logout", headers=csrf_headers(client))
    login(client, owner)

    reply = client.post(f"/api/support/{ticket_id}/messages", headers=csrf_headers(client), json={"body": "The problem is still happening."})
    assert reply.status_code == 201, reply.text
    reopened = client.get(f"/api/support/{ticket_id}").json()
    assert reopened["status"] == "new"
    assert reopened["messages"][-1]["body"] == "The problem is still happening."


def test_support_validation_and_rate_limit(client: TestClient):
    assert client.post("/api/support", json=ticket_payload(category="not-a-category")).status_code == 422
    assert client.post("/api/support", json=ticket_payload(category="other")).status_code == 422
    assert client.post("/api/support", json=ticket_payload(description="short")).status_code == 422

    for index in range(5):
        response = client.post("/api/support", json=ticket_payload(subject=f"Mapping complaint {index}"))
        assert response.status_code == 201, response.text
    limited = client.post("/api/support", json=ticket_payload(subject="One more mapping issue"))
    assert limited.status_code == 429


def test_product_insights_return_aggregate_metadata_only(client: TestClient):
    admin = create_user(username="insightsadmin", role="super_admin", mfa=True)
    login(client, admin)
    response = client.get("/api/admin/product-insights")
    assert response.status_code == 200, response.text
    assert response.json()["saved_setups"]["total"] == 0
    assert "description" not in response.json()


def test_admin_directory_is_filtered_and_sensitive_actions_require_reason(client: TestClient):
    user = create_user(username="directorymember")
    admin = create_user(username="directoryadmin", role="super_admin", mfa=True)
    login(client, admin)

    directory = client.get("/api/admin/users?role=user&sort=runs_desc&page=1&page_size=25&runs_min=0&storage_min=0")
    assert directory.status_code == 200, directory.text
    assert directory.json()["total"] == 1
    assert directory.json()["users"][0]["id"] == user.id

    headers = csrf_headers(client)
    assert client.post(f"/api/admin/users/{user.id}/revoke-sessions", headers=headers, json={}).status_code == 422
    assert client.post(f"/api/admin/users/{user.id}/revoke-sessions", headers=headers, json={"reason": "     "}).status_code == 422
    changed = client.post(f"/api/admin/users/{user.id}/revoke-sessions", headers=headers, json={"reason": "User requested all sessions be closed."})
    assert changed.status_code == 200, changed.text

    protected = client.post(f"/api/admin/users/{admin.id}/revoke-sessions", headers=headers, json={"reason": "Routine administrator cleanup."})
    assert protected.status_code == 409

    db = SessionLocal()
    try:
        event = db.query(AdminAuditEvent).filter(AdminAuditEvent.target_id == user.id, AdminAuditEvent.action == "user.sessions_revoked").one()
        assert json.loads(event.metadata_json)["reason"] == "User requested all sessions be closed."
    finally:
        db.close()
