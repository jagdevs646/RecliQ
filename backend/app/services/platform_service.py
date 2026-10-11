from __future__ import annotations

import os
import logging
import hmac
import re
from pathlib import Path
import pyotp
from dotenv.parser import parse_stream
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified

from app.core import secret_box
from app.core.config import Settings, get_settings
from app.database.session import SessionLocal
from app.models.account import User, UserPreference
from app.services.account_security import hash_password, verify_password
from app.services.policy_service import get_global_policy


logger = logging.getLogger("recliq.platform")
_BOOTSTRAP_FIELDS = {
    "BOOTSTRAP_ADMIN_USERNAME": "bootstrap_admin_username",
    "BOOTSTRAP_ADMIN_EMAIL": "bootstrap_admin_email",
    "BOOTSTRAP_ADMIN_PASSWORD": "bootstrap_admin_password",
    "BOOTSTRAP_ADMIN_TOTP_SECRET": "bootstrap_admin_totp_secret",
}


def _diagnose_bootstrap_environment() -> None:
    env_path = Path(Settings.model_config["env_file"])
    file_values: dict[str, str | None] = {}
    duplicates: set[str] = set()
    if env_path.is_file():
        try:
            with env_path.open(encoding="utf-8") as source:
                for binding in parse_stream(source):
                    line = binding.original.string.lstrip()
                    candidate = re.split(r"[\s=]", line, maxsplit=1)[0]
                    if binding.error:
                        if candidate.startswith("BOOTSTRAP_ADMIN_"):
                            logger.error(
                                "Malformed %s entry in backend/.env at line %s; use KEY=value syntax (value redacted)",
                                candidate,
                                binding.original.line,
                            )
                        continue
                    if binding.key not in _BOOTSTRAP_FIELDS:
                        continue
                    if binding.key in file_values:
                        duplicates.add(binding.key)
                    file_values[binding.key] = binding.value
        except OSError as exc:
            logger.warning("Could not inspect backend/.env for admin setup (error_type=%s)", type(exc).__name__)
    for name in sorted(duplicates):
        logger.error("Duplicate %s entry in backend/.env; remove duplicate settings (values redacted)", name)
    for name in _BOOTSTRAP_FIELDS:
        process_value = os.environ.get(name)
        file_value = file_values.get(name)
        if process_value is not None and file_value is not None and process_value != file_value:
            logger.warning("Process environment overrides %s from backend/.env (values redacted)", name)


def encrypt_stored_mfa_secrets(db) -> int:
    """Encrypt two-factor keys stored in plain text, and re-encrypt all of them
    under the first key when several are configured (key rotation)."""
    if not secret_box.encryption_enabled():
        logger.warning("MFA_ENCRYPTION_KEY is not set: two-factor keys are stored unencrypted")
        return 0
    rotating = len(get_settings().mfa_encryption_keys) > 1
    raw = db.execute(text("SELECT id, mfa_secret, mfa_pending_secret FROM users")).all()
    pending = [
        user_id for user_id, *values in raw
        if any(value and (rotating or not secret_box.is_encrypted(value)) for value in values)
    ]
    for user_id in pending:
        user = db.get(User, user_id)
        for field in ("mfa_secret", "mfa_pending_secret"):
            if getattr(user, field):
                flag_modified(user, field)  # Written back through the encrypting column type.
    db.commit()
    if pending:
        logger.info("Encrypted stored two-factor keys", extra={"accounts": len(pending)})
    return len(pending)


def initialize_platform_state() -> None:
    """Seed platform defaults and provision only explicitly configured admins."""
    settings = get_settings()
    db = SessionLocal()
    try:
        _diagnose_bootstrap_environment()
        get_global_policy(db, create=True)
        configured = {
            name: getattr(settings, field)
            for name, field in _BOOTSTRAP_FIELDS.items()
        }
        present = {name: bool(value and value.strip()) for name, value in configured.items()}
        if any(present.values()) and not all(present.values()):
            missing = ", ".join(name for name, is_set in present.items() if not is_set)
            logger.error("Admin bootstrap is incomplete; set the missing variables: %s (values redacted)", missing)
        elif all(present.values()):
            username = configured["BOOTSTRAP_ADMIN_USERNAME"].strip()
            email_input = configured["BOOTSTRAP_ADMIN_EMAIL"].strip()
            password = configured["BOOTSTRAP_ADMIN_PASSWORD"]
            totp_secret = configured["BOOTSTRAP_ADMIN_TOTP_SECRET"].strip().replace(" ", "")
            issues = []
            if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username):
                issues.append("BOOTSTRAP_ADMIN_USERNAME must be 3-64 letters, numbers, dots, underscores, or hyphens")
            if len(password) < 12:
                issues.append("BOOTSTRAP_ADMIN_PASSWORD must contain at least 12 characters")
            try:
                email = validate_email(email_input, check_deliverability=False).normalized
            except EmailNotValidError:
                issues.append("BOOTSTRAP_ADMIN_EMAIL must be a valid email address")
                email = ""
            try:
                pyotp.TOTP(totp_secret).now()
            except Exception:
                issues.append("BOOTSTRAP_ADMIN_TOTP_SECRET must be a valid base32 TOTP secret")
            if issues:
                for issue in issues:
                    logger.error("Admin bootstrap configuration invalid: %s (values redacted)", issue)
            else:
                username_key = username.casefold()
                email_key = email.casefold()
                existing_username = db.scalar(select(User).where(User.username_key == username_key).limit(1))
                existing_email = db.scalar(select(User).where(User.email_key == email_key).limit(1))
                if existing_username:
                    if existing_username.role == "super_admin" and existing_username.email_key == email_key:
                        password_matches = verify_password(password, existing_username.password_hash)
                        totp_matches = bool(
                            existing_username.mfa_secret
                            and hmac.compare_digest(totp_secret, existing_username.mfa_secret)
                        )
                        if password_matches and totp_matches:
                            logger.info("Configured Super Admin already exists; its password and TOTP secret were not changed")
                        else:
                            logger.warning("Bootstrap credentials differ from the existing Super Admin; stored password and TOTP secret were not changed")
                    else:
                        logger.error("Admin bootstrap conflict: configured username belongs to another account; no account was changed")
                elif existing_email:
                    credentials_match = (
                        existing_email.role == "super_admin"
                        and existing_email.status == "active"
                        and existing_email.email_verified
                        and existing_email.mfa_enabled
                        and bool(existing_email.mfa_secret)
                        and verify_password(password, existing_email.password_hash)
                        and hmac.compare_digest(totp_secret, existing_email.mfa_secret)
                    )
                    if credentials_match:
                        logger.warning("Configured email matches an existing Super Admin, but the configured username differs; sign in using the account email. No account fields were changed")
                    else:
                        logger.error("Admin bootstrap conflict: configured email belongs to another account; no account was changed")
                else:
                    user = User(
                        username=username,
                        username_key=username_key,
                        email=email,
                        email_key=email_key,
                        display_name=username,
                        password_hash=hash_password(password),
                        role="super_admin",
                        status="active",
                        email_verified=True,
                        mfa_enabled=True,
                        mfa_secret=totp_secret,
                    )
                    db.add(user)
                    try:
                        db.flush()
                        db.add(UserPreference(user_id=user.id))
                    except IntegrityError:
                        db.rollback()
                        get_global_policy(db, create=True)
                        logger.error("Admin bootstrap conflict: username or email was claimed concurrently; no account was changed")
                    else:
                        logger.info("Provisioned the configured Super Admin; existing administrators were not changed")
        db.commit()
        encrypt_stored_mfa_secrets(db)
    except Exception as exc:
        db.rollback()
        logger.error("Platform initialization failed (error_type=%s; sensitive values redacted)", type(exc).__name__)
        raise
    finally:
        db.close()
