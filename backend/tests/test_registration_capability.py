from __future__ import annotations

from app.core.config import get_settings


def _can_register(client) -> bool:
    return client.get("/api/auth/me").json()["capabilities"]["registration"]


def test_sign_up_is_offered_when_registration_is_on_even_without_email_delivery(client, monkeypatch):
    assert _can_register(client)

    monkeypatch.setattr(get_settings(), "registration_enabled", False)
    assert not _can_register(client)

    monkeypatch.setattr(get_settings(), "registration_enabled", True)
    monkeypatch.setattr(get_settings(), "email_backend", "disabled")
    assert _can_register(client)
    response = client.post("/api/auth/register", json={
        "username": "newcomer", "email": "newcomer@example.com", "display_name": "Newcomer",
        "password": "strong-test-password-8742",
    })
    assert response.status_code == 201, response.text
    assert response.json()["approval_required"] is True

    monkeypatch.setattr(get_settings(), "registration_enabled", False)
    unavailable = client.post("/api/auth/register", json={
        "username": "blockedmember", "email": "blockedmember@example.com", "display_name": "Blocked Member",
        "password": "strong-test-password-8742",
    })
    assert unavailable.status_code == 403
