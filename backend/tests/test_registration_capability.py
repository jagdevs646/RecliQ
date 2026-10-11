from __future__ import annotations

from app.core.config import get_settings


def _can_register(client) -> bool:
    return client.get("/api/auth/me").json()["capabilities"]["registration"]


def test_sign_up_is_offered_only_when_registration_is_on_and_email_works(client, monkeypatch):
    assert _can_register(client)  # tests: registration on, local email log

    monkeypatch.setattr(get_settings(), "registration_enabled", False)
    assert not _can_register(client)

    monkeypatch.setattr(get_settings(), "registration_enabled", True)
    monkeypatch.setattr(get_settings(), "email_backend", "disabled")  # no verification email
    assert not _can_register(client)
    response = client.post("/api/auth/register", json={
        "username": "newcomer", "email": "newcomer@example.com", "display_name": "Newcomer",
        "password": "strong-test-password-8742",
    })
    assert response.status_code == 503  # the server refuses too
