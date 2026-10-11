"""Encryption at rest for two-factor (TOTP) keys.

Values are sealed with AES-256-GCM under a key derived from MFA_ENCRYPTION_KEY
and stored as ``enc1:<base64 of nonce + ciphertext>``. A stored value without
that prefix is a key saved before encryption existed; it is read as it is and
re-encrypted at start-up (``platform_service.encrypt_stored_mfa_secrets``).

Without a key (production with MFA_ENCRYPTION_KEY unset) values are stored as
before, start-up logs a warning and the admin health page reports it.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from app.core.config import get_settings

logger = logging.getLogger("recliq.security")
PREFIX = "enc1:"
_NONCE_BYTES = 12


def _keys() -> list[bytes]:
    return [hashlib.sha256(key.encode("utf-8")).digest() for key in get_settings().mfa_encryption_keys]


def encryption_enabled() -> bool:
    return bool(get_settings().mfa_encryption_keys)


def is_encrypted(value: str) -> bool:
    return value.startswith(PREFIX)


def encrypt(value: str) -> str:
    keys = _keys()
    if not keys:
        return value
    nonce = os.urandom(_NONCE_BYTES)
    sealed = AESGCM(keys[0]).encrypt(nonce, value.encode("utf-8"), PREFIX.encode("ascii"))
    return PREFIX + base64.urlsafe_b64encode(nonce + sealed).decode("ascii")


def decrypt(value: str) -> str | None:
    """The plain value, or None when no configured key opens it (logged)."""
    if not is_encrypted(value):
        return value
    try:
        raw = base64.urlsafe_b64decode(value[len(PREFIX):])
    except ValueError:
        logger.error("A stored two-factor key is damaged and cannot be read")
        return None
    for key in _keys():
        try:
            return AESGCM(key).decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], PREFIX.encode("ascii")).decode("utf-8")
        except InvalidTag:
            continue
    # Failing closed for this one value keeps every other account working;
    # the affected account cannot pass two-factor sign-in until the key is fixed.
    logger.error("A stored two-factor key cannot be decrypted with MFA_ENCRYPTION_KEY")
    return None


class EncryptedString(TypeDecorator):
    """A string column the database only ever sees encrypted."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt(value)

    def process_result_value(self, value, dialect):
        return None if value is None else decrypt(value)
