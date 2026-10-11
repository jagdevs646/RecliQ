from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.upload_validation import SUPPORTED_FORMATS
from app.models.account import PlatformPolicy, User, UserPolicyOverride
from app.services.email_service import email_transport_status


FEATURE_DEFAULTS: dict[str, bool] = {
    "basic_reconciliation": True,
    "gst_reconciliation": True,
    "near_match_review": True,
    "auto_resolution": True,
    "saved_setups": True,
    "report_email": False,
    "registration": True,
    "social_login_google": False,
    "social_login_facebook": False,
    "social_login_linkedin": False,
}


def default_limits() -> dict[str, Any]:
    settings = get_settings()
    return {
        "guest": {
            "max_file_size_mb": settings.max_upload_mb,
            "max_files_per_run": 10,
            "max_rows_per_sheet": settings.max_rows_per_sheet,
            "max_rows_per_run": 20_000_000,
            "jobs_per_day": 100_000,
            "jobs_per_month": 1_000_000,
            "concurrent_jobs": 100,
            "storage_mb": 100_000,
            "report_retention_days": 0,
            "report_emails_per_day": 0,
            "max_email_recipients": 0,
            "allowed_formats": list(SUPPORTED_FORMATS),
        },
        "registered": {
            "max_file_size_mb": settings.max_upload_mb,
            "max_files_per_run": 20,
            "max_rows_per_sheet": settings.max_rows_per_sheet,
            "max_rows_per_run": 2_000_000,
            "jobs_per_day": 100,
            "jobs_per_month": 2000,
            "concurrent_jobs": 5,
            "storage_mb": 10_240,
            "report_retention_days": 365,
            "report_emails_per_day": 10,
            "max_email_recipients": 5,
            "allowed_formats": list(SUPPORTED_FORMATS),
        },
    }


def _decode(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


def get_global_policy(db: Session, *, create: bool = False) -> PlatformPolicy | None:
    policy = db.get(PlatformPolicy, "global")
    if policy is None and create:
        policy = PlatformPolicy(
            id="global",
            feature_flags_json=json.dumps(FEATURE_DEFAULTS),
            limits_json=json.dumps(default_limits()),
            default_theme="system",
        )
        db.add(policy)
        db.flush()
    return policy


def effective_features(db: Session, user: User | None) -> dict[str, bool]:
    settings = get_settings()
    policy = get_global_policy(db)
    features = {**FEATURE_DEFAULTS, **_decode(policy.feature_flags_json if policy else None, {})}
    if user:
        override = db.get(UserPolicyOverride, user.id)
        for key, value in _decode(override.feature_overrides_json if override else None, {}).items():
            if key in features:
                features[key] = features[key] and bool(value)
        if user.status == "restricted":
            for feature in ("gst_reconciliation", "near_match_review", "auto_resolution", "saved_setups", "report_email"):
                features[feature] = False
    if not settings.registration_enabled:
        features["registration"] = False
    if not email_transport_status(db)["ready"]:
        features["report_email"] = False
    for name in ("google", "facebook", "linkedin"):
        if not (getattr(settings, f"{name}_client_id") and getattr(settings, f"{name}_client_secret")):
            features[f"social_login_{name}"] = False
    return features


def effective_limits(db: Session, user: User | None) -> dict[str, Any]:
    policy = get_global_policy(db)
    configured = _decode(policy.limits_json if policy else None, default_limits())
    category = "registered" if user else "guest"
    limits = {**default_limits()[category], **(configured.get(category) or {})}
    if user:
        override = db.get(UserPolicyOverride, user.id)
        overrides = _decode(override.limits_overrides_json if override else None, {})
        for key, value in overrides.items():
            if key == "allowed_formats":
                limits[key] = sorted(set(limits.get(key, [])) & set(value))
            elif key in limits and isinstance(value, int) and not isinstance(value, bool):
                limits[key] = value
    # The environment variables remain hard ceilings even when a policy is
    # configured more permissively in the administrator interface.
    settings = get_settings()
    limits["max_file_size_mb"] = min(limits["max_file_size_mb"], settings.max_upload_mb)
    limits["max_rows_per_sheet"] = min(limits["max_rows_per_sheet"], settings.max_rows_per_sheet)
    return limits


def require_feature(db: Session, user: User | None, feature: str) -> None:
    if not effective_features(db, user).get(feature, False):
        raise HTTPException(status_code=403, detail={"code": "feature_disabled", "feature": feature, "message": "This feature is currently unavailable in this workspace."})


def require_active_user(user: User) -> None:
    if user.status == "suspended":
        raise HTTPException(status_code=403, detail="This account is suspended. Contact support for assistance.")
    if user.status == "deleted":
        raise HTTPException(status_code=403, detail="This account is no longer available.")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
