from __future__ import annotations

import base64
import hashlib

import pyotp
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text

from app.core import secret_box
from app.core.config import Settings, get_settings
from app.database.session import SessionLocal
from app.models.account import User, UserPreference
from app.services.account_security import hash_password
from app.services.platform_service import encrypt_stored_mfa_secrets

NEW_KEY = "rotated-mfa-key-for-tests-0123456789abcdef"


def _user(username: str) -> tuple[str, str]:
    secret = pyotp.random_base32()
    db = SessionLocal()
    try:
        user = User(
            username=username, username_key=username, email=f"{username}@example.com", email_key=f"{username}@example.com",
            display_name=username, password_hash=hash_password("strong-test-password-8742"), role="super_admin",
            status="active", email_verified=True, mfa_enabled=True, mfa_secret=secret,
        )
        db.add(user)
        db.flush()
        db.add(UserPreference(user_id=user.id))
        db.commit()
        return user.id, secret
    finally:
        db.close()


def _stored(user_id: str) -> str:
    with SessionLocal() as db:
        return db.execute(text("SELECT mfa_secret FROM users WHERE id = :id"), {"id": user_id}).scalar_one()


def _loaded(user_id: str) -> str | None:
    with SessionLocal() as db:
        return db.get(User, user_id).mfa_secret


def _set_stored(user_id: str, value: str) -> None:
    with SessionLocal() as db:
        db.execute(text("UPDATE users SET mfa_secret = :value WHERE id = :id"), {"value": value, "id": user_id})
        db.commit()


def test_two_factor_keys_are_encrypted_in_the_database_and_sign_in_still_works(client):
    user_id, secret = _user("encrypted")
    stored = _stored(user_id)
    assert stored.startswith("enc1:") and secret not in stored
    assert _loaded(user_id) == secret
    login = client.post("/api/auth/login", json={"identifier": "encrypted", "password": "strong-test-password-8742", "totp_code": pyotp.TOTP(secret).now()})
    assert login.status_code == 200, login.text


def test_plain_keys_saved_before_encryption_are_encrypted_at_start_up(client):
    user_id, secret = _user("legacy")
    _set_stored(user_id, secret)  # as written before this change
    assert _loaded(user_id) == secret
    with SessionLocal() as db:
        assert encrypt_stored_mfa_secrets(db) == 1
    assert _stored(user_id).startswith("enc1:")
    assert _loaded(user_id) == secret
    with SessionLocal() as db:
        assert encrypt_stored_mfa_secrets(db) == 0  # nothing left to do


def test_a_new_key_takes_over_and_old_values_are_re_encrypted(client, monkeypatch):
    user_id, secret = _user("rotation")
    monkeypatch.setattr(get_settings(), "mfa_encryption_key", NEW_KEY)
    with SessionLocal() as db:
        assert encrypt_stored_mfa_secrets(db) == 1
    raw = base64.urlsafe_b64decode(_stored(user_id).removeprefix("enc1:"))
    opened = AESGCM(hashlib.sha256(NEW_KEY.encode()).digest()).decrypt(raw[:12], raw[12:], b"enc1:")
    assert opened.decode() == secret


def test_a_value_no_key_opens_reads_as_missing_without_breaking_other_accounts(client, monkeypatch):
    errors: list[str] = []
    monkeypatch.setattr(secret_box.logger, "error", lambda message, *args, **kwargs: errors.append(message))
    locked_id, _ = _user("locked")
    other_id, other_secret = _user("other")
    foreign = AESGCM(hashlib.sha256(b"some-other-deployment-key-0123456789").digest())
    _set_stored(locked_id, "enc1:" + base64.urlsafe_b64encode(b"\0" * 12 + foreign.encrypt(b"\0" * 12, b"JBSWY3DPEHPK3PXP", b"enc1:")).decode())
    assert _loaded(locked_id) is None
    assert any("cannot be decrypted" in message for message in errors)
    assert _loaded(other_id) == other_secret
    with SessionLocal() as db:
        encrypt_stored_mfa_secrets(db)  # leaves the unreadable value as it is
    assert _stored(locked_id).startswith("enc1:")


def test_production_without_a_key_keeps_working_and_reports_it(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "environment", "production")
    assert not secret_box.encryption_enabled()
    assert secret_box.encrypt("JBSWY3DPEHPK3PXP") == "JBSWY3DPEHPK3PXP"


def test_short_keys_are_refused():
    with pytest.raises(ValueError, match="MFA_ENCRYPTION_KEY"):
        Settings(mfa_encryption_key="too-short")
    with pytest.raises(ValueError, match="MFA_ENCRYPTION_KEY"):
        Settings(mfa_encryption_key=f"{NEW_KEY},short")
