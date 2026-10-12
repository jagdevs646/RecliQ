from __future__ import annotations

import json
import base64
import hashlib
import hmac
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

from authlib.integrations.requests_client import OAuth2Session
import pyotp
from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.anonymous_session import SESSION_COOKIE_NAME, validate_session_id
from app.core.config import get_settings
from app.database.session import get_db
from app.models.account import AuthIdentity, AuthToken, ReportEmailDelivery, User, UserPreference, UserSession, WorkspaceImport
from app.models.alias import EntityAlias, MatchDecision
from app.models.file import UploadedFile
from app.models.history import ReconciliationHistory
from app.models.job import ReconciliationJob
from app.models.report import Report
from app.models.resolution import ExceptionPattern, ResolutionRule
from app.models.template import ReconciliationTemplate
from app.schemas.account import ChangeEmailRequest, ChangePasswordRequest, DeleteAccountRequest, ForgotPasswordRequest, ImportGuestRequest, LoginRequest, MfaCodeRequest, MfaDisableRequest, ProfileUpdateRequest, RegisterRequest, ResetPasswordRequest, ThemeUpdateRequest, TokenRequest
from app.services.account_security import check_auth_rate_limit, clear_auth_failures, create_login_session, hash_password, new_token, public_user, record_admin_event, record_auth_attempt, record_auth_failure, token_digest, verify_password, verify_totp
from app.services.audit_service import AuditActor, record_event
from app.services.email_service import email_transport_status, send_email
from app.services.policy_service import effective_features
from app.services.policy_service import effective_limits
from app.services.quota_service import merge_workspace_usage, release_storage, reserve_storage
from app.storage import get_storage
from app.storage.base import report_data_storage_paths


router = APIRouter(prefix="/auth", tags=["authentication"])
logger = logging.getLogger("recliq.auth")


def _normal_email(value: str) -> str:
    try:
        return validate_email(value, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise HTTPException(status_code=422, detail="Enter a valid email address.") from exc


def _new_auth_token(db: Session, user: User, purpose: str, *, destination_email: str | None = None, ttl_hours: int = 24) -> str:
    raw = new_token()
    now = datetime.now(timezone.utc)
    db.add(AuthToken(
        user_id=user.id,
        token_hash=token_digest(raw),
        purpose=purpose,
        destination_email=destination_email,
        created_at=now,
        expires_at=now + timedelta(hours=ttl_hours),
    ))
    return raw


def _consume_token(db: Session, raw: str, purpose: str) -> AuthToken | None:
    now = datetime.now(timezone.utc)
    consumed = db.execute(
        update(AuthToken)
        .where(
            AuthToken.token_hash == token_digest(raw),
            AuthToken.purpose == purpose,
            AuthToken.consumed_at.is_(None),
            AuthToken.expires_at > now,
        )
        .values(consumed_at=now)
    )
    if consumed.rowcount != 1:
        return None
    return db.scalar(select(AuthToken).where(AuthToken.token_hash == token_digest(raw), AuthToken.purpose == purpose))


def _send_action_email(*, recipient: str, title: str, token: str, route: str, expires_in: str = "24 hours", log_body: bool = True) -> None:
    url = f"{get_settings().public_app_url.rstrip('/')}/{route}?token={quote(token)}"
    send_email(
        recipient=recipient,
        subject=f"RecliQ {title}",
        text_body=f"Use this link to {title.lower()}:\n\n{url}\n\nThis secure link expires in {expires_in} and can only be used once. If you did not request this, you can ignore this email.",
        log_body=log_body,
    )


def _development_action_url(token: str, route: str) -> str | None:
    settings = get_settings()
    if settings.email_backend.lower() != "log" or settings.environment.lower() not in {"development", "local", "test"}:
        return None
    return f"{settings.public_app_url.rstrip('/')}/{route}?token={quote(token)}"


def _verification_email_ready(db: Session) -> bool:
    return bool(email_transport_status(db)["ready"])


def _set_auth_cookie(response: Response, raw_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.auth_cookie_name,
        raw_token,
        max_age=settings.auth_session_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="none" if settings.session_cookie_secure else "lax",
        path="/",
    )


def _clear_auth_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.auth_cookie_name, path="/", secure=settings.session_cookie_secure, httponly=True, samesite="none" if settings.session_cookie_secure else "lax")


def _security_ready() -> None:
    settings = get_settings()
    if not settings.effective_auth_csrf_secret:
        raise HTTPException(status_code=503, detail="Account sign-in is not configured on this deployment.")


def _oauth_config(provider: str, db: Session) -> dict[str, str]:
    settings = get_settings()
    configs = {
        "google": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
            "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
            "scope": "openid email profile",
        },
        "facebook": {
            "client_id": settings.facebook_client_id,
            "client_secret": settings.facebook_client_secret,
            "authorize_url": f"https://www.facebook.com/{settings.facebook_graph_version}/dialog/oauth",
            "token_url": f"https://graph.facebook.com/{settings.facebook_graph_version}/oauth/access_token",
            "userinfo_url": f"https://graph.facebook.com/{settings.facebook_graph_version}/me",
            "scope": "email public_profile",
        },
        "linkedin": {
            "client_id": settings.linkedin_client_id,
            "client_secret": settings.linkedin_client_secret,
            "authorize_url": "https://www.linkedin.com/oauth/v2/authorization",
            "token_url": "https://www.linkedin.com/oauth/v2/accessToken",
            "userinfo_url": "https://api.linkedin.com/v2/userinfo",
            "scope": "openid profile email",
        },
    }
    if provider not in configs or not effective_features(db, None).get(f"social_login_{provider}", False):
        raise HTTPException(status_code=404, detail="This sign-in provider is not configured.")
    config = configs[provider]
    if not config["client_id"] or not config["client_secret"]:
        raise HTTPException(status_code=404, detail="This sign-in provider is not configured.")
    return config


def _oauth_callback_url(provider: str) -> str:
    settings = get_settings()
    api_root = (settings.public_api_url or f"{settings.public_app_url.rstrip('/')}{settings.api_prefix}").rstrip("/")
    return f"{api_root}/auth/oauth/{provider}/callback"


def _encode_oauth_context(context: dict) -> str:
    raw = json.dumps(context, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(get_settings().effective_auth_csrf_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _decode_oauth_context(value: str | None, provider: str, returned_state: str | None) -> dict | None:
    if not value or not returned_state:
        return None
    try:
        encoded, signature = value.rsplit(".", 1)
        expected = hmac.new(get_settings().effective_auth_csrf_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        context = json.loads(raw)
        if context.get("provider") != provider or not hmac.compare_digest(context.get("state", ""), returned_state):
            return None
        if int(context.get("issued_at", 0)) < int(time.time()) - 600:
            return None
        return context
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _oauth_client(config: dict[str, str], callback_url: str) -> OAuth2Session:
    return OAuth2Session(
        config["client_id"],
        config["client_secret"],
        token_endpoint_auth_method="client_secret_post",
        scope=config["scope"],
        redirect_uri=callback_url,
        timeout=10,
    )


def _unique_oauth_username(db: Session, email: str, provider: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "", email.split("@", 1)[0]).strip("._-") or provider
    base = base[:52]
    candidate = base
    while db.scalar(select(User.id).where(User.username_key == candidate.casefold()).limit(1)):
        candidate = f"{base[:52]}-{secrets.token_hex(3)}"
    return candidate


@router.get("/oauth/providers")
def oauth_providers(db: Session = Depends(get_db)) -> dict[str, list[dict[str, str | bool]]]:
    settings = get_settings()
    features = effective_features(db, None)
    providers = []
    for provider, label in (("google", "Google"), ("facebook", "Facebook"), ("linkedin", "LinkedIn")):
        configured = bool(getattr(settings, f"{provider}_client_id") and getattr(settings, f"{provider}_client_secret"))
        providers.append({"id": provider, "label": label, "enabled": bool(configured and features.get(f"social_login_{provider}"))})
    return {"providers": providers}


@router.get("/oauth/{provider}")
def oauth_start(
    provider: str,
    request: Request,
    mode: str = "login",
    db: Session = Depends(get_db),
) -> RedirectResponse:
    _security_ready()
    config = _oauth_config(provider, db)
    if mode not in {"login", "link"}:
        raise HTTPException(status_code=422, detail="Unknown OAuth operation.")
    check_auth_rate_limit(db, request, None, action="oauth_start")
    record_auth_attempt(db, request, None, action="oauth_start")
    user_id = getattr(request.state, "user_id", None)
    if mode == "link" and not user_id:
        raise HTTPException(status_code=401, detail="Sign in before linking a provider.")
    callback_url = _oauth_callback_url(provider)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    client = _oauth_client(config, callback_url)
    authorize_url, _ = client.create_authorization_url(
        config["authorize_url"],
        state=state,
        code_verifier=verifier,
        code_challenge_method="S256",
    )
    context = _encode_oauth_context({
        "provider": provider,
        "state": state,
        "verifier": verifier,
        "mode": mode,
        "user_id": user_id,
        "issued_at": int(time.time()),
    })
    response = RedirectResponse(authorize_url, status_code=302)
    response.set_cookie(
        f"recliq_oauth_{provider}",
        context,
        max_age=600,
        httponly=True,
        secure=get_settings().session_cookie_secure,
        samesite="lax",
        path=f"{get_settings().api_prefix}/auth/oauth/{provider}",
    )
    return response


@router.get("/oauth/{provider}/callback")
def oauth_callback(provider: str, request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    settings = get_settings()
    check_auth_rate_limit(db, request, None, action="oauth_callback")
    record_auth_attempt(db, request, None, action="oauth_callback")
    frontend = settings.public_app_url.rstrip("/")
    returned_state = request.query_params.get("state")
    cookie_name = f"recliq_oauth_{provider}"
    cookie_path = f"{settings.api_prefix}/auth/oauth/{provider}"
    context = _decode_oauth_context(request.cookies.get(cookie_name), provider, returned_state)

    def finish(location: str) -> RedirectResponse:
        response = RedirectResponse(location, status_code=303)
        response.delete_cookie(cookie_name, path=cookie_path, secure=settings.session_cookie_secure, httponly=True, samesite="lax")
        return response

    if request.query_params.get("error") or not context:
        return finish(f"{frontend}/#/dashboard?oauth_error=verification")
    try:
        config = _oauth_config(provider, db)
        code = request.query_params.get("code")
        if not code:
            return finish(f"{frontend}/#/dashboard?oauth_error=code")
        client = _oauth_client(config, _oauth_callback_url(provider))
        token = client.fetch_token(
            config["token_url"],
            code=code,
            state=returned_state,
            code_verifier=context["verifier"],
        )
        params = {"fields": "id,name,email"} if provider == "facebook" else None
        client.token = token
        info = client.get(config["userinfo_url"], params=params, timeout=10).json()
        subject = str(info.get("sub") or info.get("id") or "").strip()
        email = _normal_email(str(info.get("email") or "")) if info.get("email") else ""
        if not subject or not email:
            return finish(f"{frontend}/#/dashboard?oauth_error=profile")

        identity = db.scalar(select(AuthIdentity).where(AuthIdentity.provider == provider, AuthIdentity.provider_subject == subject))
        if context["mode"] == "link":
            user_id = context.get("user_id")
            user = db.get(User, user_id) if user_id else None
            if not user or user.role == "super_admin" or getattr(request.state, "user_id", None) != user.id:
                return finish(f"{frontend}/#/settings?oauth_error=session")
            if identity and identity.user_id != user.id:
                return finish(f"{frontend}/#/settings?oauth_error=already_linked")
            if not identity:
                db.add(AuthIdentity(user_id=user.id, provider=provider, provider_subject=subject, provider_email=email))
                db.commit()
            return finish(f"{frontend}/#/settings?oauth=linked")

        if identity:
            user = db.get(User, identity.user_id)
        else:
            if info.get("email_verified") is False:
                return finish(f"{frontend}/#/dashboard?oauth_error=unverified_email")
            if not effective_features(db, None).get("registration", False):
                return finish(f"{frontend}/#/dashboard?oauth_error=registration_disabled")
            email_verified = info.get("email_verified") is True
            if not email_verified and not _verification_email_ready(db):
                return finish(f"{frontend}/#/dashboard?oauth_error=verification_email_unavailable")
            if db.scalar(select(User.id).where(User.email_key == email.casefold()).limit(1)):
                # Never link identities solely because provider email strings match.
                return finish(f"{frontend}/#/dashboard?oauth_error=account_exists")
            display_name = str(info.get("name") or "").strip()[:120] or email.split("@", 1)[0]
            user = User(
                username=_unique_oauth_username(db, email, provider),
                username_key="",
                email=email,
                email_key=email.casefold(),
                display_name=display_name,
                password_hash=None,
                role="user",
                status="active",
                email_verified=email_verified,
            )
            user.username_key = user.username.casefold()
            verification_token = None
            db.add(user)
            try:
                db.flush()
                db.add(UserPreference(user_id=user.id))
                db.add(AuthIdentity(user_id=user.id, provider=provider, provider_subject=subject, provider_email=email))
                if not email_verified:
                    verification_token = _new_auth_token(db, user, "verify_email")
                db.commit()
            except IntegrityError:
                db.rollback()
                return finish(f"{frontend}/#/dashboard?oauth_error=account_exists")
            if verification_token:
                try:
                    _send_action_email(recipient=user.email, title="verify your email", token=verification_token, route="verify-email")
                except Exception:
                    logger.exception("OAuth verification email could not be sent", extra={"user_id": user.id, "provider": provider})
                    return finish(f"{frontend}/#/dashboard?oauth_error=verification_email")
                return finish(f"{frontend}/#/dashboard?oauth=verification_sent")

        if not user or user.status in {"suspended", "deleted"} or user.role == "super_admin":
            return finish(f"{frontend}/#/dashboard?oauth_error=account_unavailable")
        if not user.email_verified:
            return finish(f"{frontend}/#/dashboard?oauth_error=unverified_email")
        if user.mfa_enabled:
            return finish(f"{frontend}/#/dashboard?oauth_error=mfa_required")
        if user.require_password_reset:
            return finish(f"{frontend}/#/dashboard?oauth_error=password_reset_required")
        _token, _session = create_login_session(db, user, request, mfa_verified=False)
        db.commit()
        record_event(
            scope_id=f"user-{user.id}",
            actor=AuditActor("user", user.id),
            action="auth.oauth_login",
            entity_type="user",
            entity_id=user.id,
            summary=f"Signed in with {provider.title()}",
        )
        response = finish(f"{frontend}/#/dashboard?oauth=success")
        # The raw opaque token exists only in the HttpOnly cookie; the DB stores its digest.
        response.set_cookie(
            settings.auth_cookie_name,
            _token,
            max_age=settings.auth_session_days * 24 * 60 * 60,
            httponly=True,
            secure=settings.session_cookie_secure,
            samesite="none" if settings.session_cookie_secure else "lax",
            path="/",
        )
        return response
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("OAuth callback failed", extra={"provider": provider, "error_type": type(exc).__name__})
        return finish(f"{frontend}/#/dashboard?oauth_error=provider")


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    _security_ready()
    check_auth_rate_limit(db, request, None, action="registration")
    record_auth_attempt(db, request, None, action="registration")
    features = effective_features(db, None)
    if not features.get("registration", False):
        raise HTTPException(status_code=403, detail="New account registration is temporarily unavailable.")
    if not _verification_email_ready(db):
        raise HTTPException(status_code=503, detail="Account verification email is not configured.")

    username = payload.username.strip()
    username_key = username.casefold()
    email = _normal_email(str(payload.email))
    email_key = email.casefold()
    if db.scalar(select(User.id).where((User.username_key == username_key) | (User.email_key == email_key)).limit(1)):
        raise HTTPException(status_code=409, detail="That username or email is already registered.")
    if payload.password.casefold() in {username_key, email_key} or len(set(payload.password)) < 5:
        raise HTTPException(status_code=422, detail="Choose a longer password that is not based on your username or email.")

    user = User(
        username=username,
        username_key=username_key,
        email=email,
        email_key=email_key,
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
        role="user",
        status="active",
        email_verified=False,
    )
    db.add(user)
    try:
        db.flush()
        db.add(UserPreference(user_id=user.id))
        raw = _new_auth_token(db, user, "verify_email")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="That username or email is already registered.") from exc
    try:
        _send_action_email(recipient=user.email, title="verify your email", token=raw, route="verify-email")
    except Exception as exc:
        logger.exception("Verification email could not be sent", extra={"user_id": user.id})
        raise HTTPException(status_code=503, detail="Your account was created, but the verification email could not be sent. Use Resend verification after email delivery is configured.") from exc
    result = {"message": "Account created. Check your email to verify it before signing in.", "email": user.email}
    development_url = _development_action_url(raw, "verify-email")
    if development_url:
        result["message"] = "Account created. Use the local verification link below, then sign in. No email was sent."
        result["development_verification_url"] = development_url
    return result


@router.post("/verify-email")
def verify_email(payload: TokenRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    token = _consume_token(db, payload.token, "verify_email")
    if not token:
        raise HTTPException(status_code=400, detail="This verification link is invalid or has expired.")
    user = db.get(User, token.user_id)
    if not user:
        raise HTTPException(status_code=400, detail="This verification link is invalid or has expired.")
    user.email_verified = True
    db.commit()
    return {"message": "Email verified. You can now sign in."}


@router.post("/verification/resend")
def resend_verification(payload: ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    check_auth_rate_limit(db, request, None, action="verification_resend")
    record_auth_attempt(db, request, None, action="verification_resend")
    email_key = _normal_email(str(payload.email)).casefold()
    user = db.scalar(select(User).where(User.email_key == email_key))
    message = "If the account needs verification, a new link has been sent."
    result: dict[str, str] = {"message": message}
    if user and not user.email_verified:
        raw = _new_auth_token(db, user, "verify_email")
        db.commit()
        try:
            _send_action_email(recipient=user.email, title="verify your email", token=raw, route="verify-email")
        except Exception:
            logger.exception("Verification email resend failed", extra={"user_id": user.id})
        development_url = _development_action_url(raw, "verify-email")
        if development_url:
            result["message"] = "Local verification link ready. No email was sent."
            result["development_verification_url"] = development_url
    return result


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    _security_ready()
    identity = payload.identifier.strip().casefold()
    check_auth_rate_limit(db, request, identity, action="login")
    user = db.scalar(select(User).where((User.username_key == identity) | (User.email_key == identity)))
    if not user or not verify_password(payload.password, user.password_hash):
        record_auth_failure(db, request, identity, action="login")
        raise HTTPException(status_code=401, detail="The username/email or password is incorrect.")
    if user.status in {"suspended", "deleted"}:
        raise HTTPException(status_code=403, detail="This account is not currently available. Contact support for assistance.")
    if not user.email_verified:
        raise HTTPException(status_code=403, detail="Verify your email before signing in.")
    admin = user.role == "super_admin"
    if admin and (not user.mfa_enabled or not user.mfa_secret):
        raise HTTPException(status_code=403, detail="Administrator MFA is not configured. Contact the platform owner.")
    if user.mfa_enabled or admin:
        if not payload.totp_code:
            raise HTTPException(status_code=428, detail="Enter your authenticator code to finish signing in.")
        if not verify_totp(user, payload.totp_code):
            record_auth_failure(db, request, identity, action="login")
            raise HTTPException(status_code=401, detail="The authenticator code is invalid or expired.")
    if user.require_password_reset:
        raise HTTPException(status_code=403, detail="A password reset is required before this account can sign in.")

    token, user_session = create_login_session(db, user, request, mfa_verified=bool(user.mfa_enabled or admin))
    if admin:
        record_admin_event(db, request, actor_id=user.id, action="auth.admin_login", target_type="user", target_id=user.id, after={"session_id": user_session.id})
    db.commit()
    clear_auth_failures(db, request, identity, action="login")
    if not admin:
        # Release SQLite's writer lock before the audit service opens its own
        # transaction. PostgreSQL can handle both writers, SQLite cannot.
        record_event(scope_id=f"user-{user.id}", actor=AuditActor("user", user.id), action="auth.login", entity_type="user", entity_id=user.id, summary="User signed in")
    _set_auth_cookie(response, token)
    return {"authenticated": True, "user": public_user(user), "workspace": {"type": "account", "id": f"user-{user.id}"}}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Response:
    session_id = getattr(request.state, "auth_session_id", None)
    user_id = getattr(request.state, "user_id", None)
    if session_id:
        session = db.get(UserSession, session_id)
        if session and not session.revoked_at:
            session.revoked_at = datetime.now(timezone.utc)
            if user_id and getattr(request.state, "user_role", None) == "super_admin":
                record_admin_event(db, request, actor_id=user_id, action="auth.admin_logout", target_type="user", target_id=user_id, after={"session_id": session_id})
            db.commit()
    _clear_auth_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me")
def current_identity(request: Request, db: Session = Depends(get_db)) -> dict:
    user_id = getattr(request.state, "user_id", None)
    guest_id = getattr(request.state, "session_id", None)
    features = effective_features(db, db.get(User, user_id) if user_id else None)
    notifications_enabled = bool(email_transport_status(db)["ready"])
    if not user_id:
        # Sign-up needs registration allowed and a working verification email
        # (see register()); the frontend offers "Create account" only then.
        capabilities = {
            "report_email": features.get("report_email", False),
            "email_notifications": notifications_enabled,
            "registration": bool(features.get("registration", False) and notifications_enabled),
        }
        return {"authenticated": False, "user": None, "workspace": {"type": "guest", "id": guest_id, "label": "Guest Workspace"}, "features": features, "capabilities": capabilities}
    user = db.get(User, user_id)
    preference = db.get(UserPreference, user_id)
    return {
        "authenticated": True,
        "user": public_user(user, theme=preference.theme if preference else "system"),
        "workspace": {"type": "account", "id": f"user-{user.id}", "label": user.display_name},
        "features": effective_features(db, user),
        "capabilities": {"report_email": features.get("report_email", False), "email_notifications": notifications_enabled},
    }


@router.post("/password/forgot")
def request_password_reset(payload: ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    _security_ready()
    email = _normal_email(str(payload.email))
    check_auth_rate_limit(db, request, None, action="password_forgot")
    record_auth_attempt(db, request, None, action="password_forgot")
    user = db.scalar(select(User).where(User.email_key == email.casefold(), User.status != "deleted"))
    if user:
        now = datetime.now(timezone.utc)
        db.query(AuthToken).filter(
            AuthToken.user_id == user.id,
            AuthToken.purpose == "password_reset",
            AuthToken.consumed_at.is_(None),
        ).update({AuthToken.consumed_at: now}, synchronize_session=False)
        raw = _new_auth_token(db, user, "password_reset", ttl_hours=1)
        db.commit()
        try:
            _send_action_email(recipient=user.email, title="reset your password", token=raw, route="reset-password", expires_in="1 hour", log_body=False)
        except Exception as exc:
            logger.warning("Password reset email could not be sent", extra={"user_id": user.id, "error_type": type(exc).__name__})
            db.rollback()
        development_url = _development_action_url(raw, "reset-password")
        if development_url:
            return {"message": "Local password reset link ready. No email was sent.", "development_reset_url": development_url}
    return {"message": "If an account exists for that email, password reset instructions have been sent."}


@router.post("/password/reset")
def reset_password(payload: ResetPasswordRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    check_auth_rate_limit(db, request, None, action="password_reset")
    record_auth_attempt(db, request, None, action="password_reset")
    token = _consume_token(db, payload.token, "password_reset")
    if not token:
        raise HTTPException(status_code=400, detail="This password reset link is invalid or has expired.")
    user = db.get(User, token.user_id)
    if not user or user.status in {"suspended", "deleted"}:
        raise HTTPException(status_code=400, detail="This password reset link is invalid or has expired.")
    user.password_hash = hash_password(payload.new_password)
    user.require_password_reset = False
    now = datetime.now(timezone.utc)
    db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).update({UserSession.revoked_at: now}, synchronize_session=False)
    db.commit()
    return {"message": "Password updated. Sign in with your new password."}


@router.post("/password/change")
def change_password(payload: ChangePasswordRequest, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, str]:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="The current password is incorrect.")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=422, detail="Choose a new password that differs from the current one.")
    user.password_hash = hash_password(payload.new_password)
    user.require_password_reset = False
    current_id = getattr(request.state, "auth_session_id", None)
    now = datetime.now(timezone.utc)
    db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None), UserSession.id != current_id).update({UserSession.revoked_at: now}, synchronize_session=False)
    db.commit()
    record_event(scope_id=f"user-{user.id}", actor=AuditActor("user", user.id), action="auth.password_changed", entity_type="user", entity_id=user.id, summary="Password changed")
    return {"message": "Password changed. Other active sessions were signed out."}


@router.put("/profile")
def update_profile(payload: ProfileUpdateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    user.display_name = payload.display_name.strip()
    db.commit()
    db.refresh(user)
    return {"user": public_user(user)}


@router.post("/account/delete")
def delete_own_account(
    payload: DeleteAccountRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    if user.role == "super_admin":
        raise HTTPException(status_code=403, detail="A Super Admin account cannot be deleted through user settings.")
    if user.password_hash and not verify_password(payload.current_password or "", user.password_hash):
        raise HTTPException(status_code=400, detail="The current password is incorrect.")
    if not payload.confirm:
        raise HTTPException(status_code=422, detail="Confirm account deletion.")
    before = {"status": user.status}
    user.status = "deleted"
    now = datetime.now(timezone.utc)
    db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).update({UserSession.revoked_at: now}, synchronize_session=False)
    db.commit()
    record_event(scope_id=f"user-{user.id}", actor=AuditActor("user", user.id), action="auth.account_deletion_requested", entity_type="user", entity_id=user.id, summary="Account access was disabled by the account owner", before=before, after={"status": "deleted", "data_retained": True})
    _clear_auth_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/email/change")
def change_email(payload: ChangeEmailRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, str]:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="The current password is incorrect.")
    email = _normal_email(str(payload.new_email))
    email_key = email.casefold()
    if db.scalar(select(User.id).where(User.email_key == email_key, User.id != user.id).limit(1)):
        raise HTTPException(status_code=409, detail="That email is already linked to another account.")
    token = _new_auth_token(db, user, "email_change", destination_email=email, ttl_hours=24)
    db.commit()
    try:
        _send_action_email(recipient=email, title="verify your new email", token=token, route="verify-email-change")
    except Exception as exc:
        logger.exception("Email change verification message could not be sent", extra={"user_id": user.id})
        raise HTTPException(status_code=503, detail="The verification email could not be sent. Try again after email service is available.") from exc
    return {"message": "Check the new email address to confirm the change."}


@router.post("/email/change/verify")
def verify_email_change(payload: TokenRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, str]:
    token = _consume_token(db, payload.token, "email_change")
    if not token or token.user_id != user.id or not token.destination_email:
        raise HTTPException(status_code=400, detail="This email-change link is invalid or has expired.")
    normalized = _normal_email(token.destination_email)
    user.email = normalized
    user.email_key = normalized.casefold()
    user.email_verified = True
    db.commit()
    return {"message": "Email address updated."}


@router.get("/sessions")
def list_sessions(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    now = datetime.now(timezone.utc)
    sessions = db.scalars(select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None), UserSession.expires_at > now).order_by(UserSession.last_seen_at.desc())).all()
    current = getattr(request.state, "auth_session_id", None)
    return {"sessions": [{
        "id": item.id,
        "created_at": item.created_at,
        "last_seen_at": item.last_seen_at,
        "expires_at": item.expires_at,
        "user_agent": item.user_agent,
        "current": item.id == current,
    } for item in sessions]}


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(session_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Response:
    session = db.scalar(select(UserSession).where(UserSession.id == session_id, UserSession.user_id == user.id, UserSession.revoked_at.is_(None)))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    session.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/mfa/setup")
def setup_mfa(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, str]:
    if user.role == "super_admin":
        raise HTTPException(status_code=403, detail="Administrator MFA is managed by the secure provisioning process.")
    secret = pyotp.random_base32()
    user.mfa_pending_secret = secret
    db.commit()
    label = quote(f"RecliQ:{user.email}")
    issuer = quote("RecliQ")
    uri = f"otpauth://totp/{label}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"
    return {"secret": secret, "otpauth_url": uri}


@router.post("/mfa/enable")
def enable_mfa(payload: MfaCodeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, str]:
    if not user.mfa_pending_secret or not pyotp.TOTP(user.mfa_pending_secret).verify(payload.code, valid_window=1):
        raise HTTPException(status_code=400, detail="The verification code is invalid.")
    user.mfa_secret = user.mfa_pending_secret
    user.mfa_pending_secret = None
    user.mfa_enabled = True
    db.commit()
    return {"message": "Multi-factor authentication is enabled."}


@router.post("/mfa/disable")
def disable_mfa(payload: MfaDisableRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, str]:
    if user.role == "super_admin":
        raise HTTPException(status_code=403, detail="Administrator multi-factor authentication cannot be disabled.")
    if not verify_password(payload.password, user.password_hash) or not verify_totp(user, payload.code):
        raise HTTPException(status_code=400, detail="The password or verification code is incorrect.")
    user.mfa_enabled = False
    user.mfa_secret = None
    user.mfa_pending_secret = None
    db.commit()
    return {"message": "Multi-factor authentication is disabled."}


@router.get("/preferences")
def get_preferences(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    preference = db.get(UserPreference, user.id)
    if not preference:
        preference = UserPreference(user_id=user.id)
        db.add(preference)
        db.commit()
    return {"theme": preference.theme, "notifications": json.loads(preference.notifications_json or "{}")}


@router.put("/preferences")
def update_preferences(payload: ThemeUpdateRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    preference = db.get(UserPreference, user.id)
    if not preference:
        preference = UserPreference(user_id=user.id)
        db.add(preference)
    preference.theme = payload.theme
    if payload.notifications is not None:
        allowed = {"reconciliation_completed", "reconciliation_failed", "report_email_status"}
        preference.notifications_json = json.dumps({key: bool(value) for key, value in payload.notifications.items() if key in allowed})
    db.commit()
    return {"theme": preference.theme, "notifications": json.loads(preference.notifications_json or "{}")}


@router.post("/import-guest")
def import_guest_workspace(
    payload: ImportGuestRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    if not payload.confirm:
        raise HTTPException(status_code=422, detail="Confirm the guest workspace import before continuing.")
    guest_scope = validate_session_id(request.cookies.get(SESSION_COOKIE_NAME))
    if not guest_scope:
        raise HTTPException(status_code=409, detail="The current browser does not have a verifiable guest workspace cookie.")
    target_scope = f"user-{user.id}"
    if db.query(WorkspaceImport).filter(WorkspaceImport.source_scope == guest_scope).first():
        raise HTTPException(status_code=409, detail="This guest workspace has already been imported.")

    files = db.query(UploadedFile).filter(UploadedFile.session_id == guest_scope).all()
    reports = db.query(Report).filter(Report.session_id == guest_scope).all()
    jobs = db.query(ReconciliationJob).filter(ReconciliationJob.session_id == guest_scope).all()
    templates = db.query(ReconciliationTemplate).filter(ReconciliationTemplate.session_id == guest_scope).all()
    aliases = db.query(EntityAlias).filter(EntityAlias.scope_id == guest_scope).all()
    decisions = db.query(MatchDecision).filter(MatchDecision.scope_id == guest_scope).all()
    rules = db.query(ResolutionRule).filter(ResolutionRule.scope_id == guest_scope).all()
    patterns = db.query(ExceptionPattern).filter(ExceptionPattern.scope_id == guest_scope).all()
    email_deliveries = db.query(ReportEmailDelivery).filter(ReportEmailDelivery.scope_id == guest_scope).all()
    counts = {"files": len(files), "reports": len(reports), "jobs": len(jobs), "saved_setups": len(templates), "aliases": len(aliases), "decisions": len(decisions), "rules": len(rules), "patterns": len(patterns), "report_emails": len(email_deliveries)}
    if not any(counts.values()):
        raise HTTPException(status_code=404, detail="There is no guest workspace data to import.")
    if any(job.status in {"queued", "processing"} for job in jobs):
        raise HTTPException(status_code=409, detail="Wait for active reconciliations to finish before importing this workspace.")
    if any(delivery.status in {"queued", "processing"} for delivery in email_deliveries):
        raise HTTPException(status_code=409, detail="Wait for queued report emails to finish before importing this workspace.")

    storage = get_storage()
    copied_paths: list[str] = []
    path_updates: list[tuple[object, str, str]] = []
    old_paths: list[str] = []
    total_storage_bytes = 0
    try:
        for record in files:
            old_path = record.storage_path
            prefix = f"uploads/{guest_scope}/"
            if not old_path.startswith(prefix):
                raise HTTPException(status_code=409, detail="A guest upload has an unexpected storage path; the import was cancelled.")
            new_path = f"uploads/{target_scope}/{old_path[len(prefix):]}"
            storage.save_file(storage.resolve_path(old_path), new_path)
            copied_paths.append(new_path)
            path_updates.append((record, old_path, new_path))
            old_paths.append(old_path)
            total_storage_bytes += record.size_bytes
        for record in reports:
            old_path = record.storage_path
            prefix = f"reports/{guest_scope}/"
            if not old_path.startswith(prefix):
                raise HTTPException(status_code=409, detail="A guest report has an unexpected storage path; the import was cancelled.")
            new_path = f"reports/{target_scope}/{old_path[len(prefix):]}"
            storage.save_file(storage.resolve_path(old_path), new_path)
            copied_paths.append(new_path)
            for old_data_path, new_data_path in zip(report_data_storage_paths(old_path), report_data_storage_paths(new_path)):
                try:
                    storage.save_file(storage.resolve_path(old_data_path), new_data_path)
                    copied_paths.append(new_data_path)
                    old_paths.append(old_data_path)
                except FileNotFoundError:
                    continue
            path_updates.append((record, old_path, new_path))
            old_paths.append(old_path)
            total_storage_bytes += record.size_bytes + record.data_size_bytes

        limits = effective_limits(db, user)
        reserve_storage(db, target_scope, total_storage_bytes, limits["storage_mb"])
        release_storage(db, guest_scope, total_storage_bytes)
        merge_workspace_usage(db, guest_scope, target_scope)
        for record, _old_path, new_path in path_updates:
            record.storage_path = new_path
            record.session_id = target_scope
        for job in jobs:
            job.session_id = target_scope
        db.query(ReconciliationHistory).filter(ReconciliationHistory.session_id == guest_scope).update({ReconciliationHistory.session_id: target_scope}, synchronize_session=False)
        db.query(ReconciliationTemplate).filter(ReconciliationTemplate.session_id == guest_scope).update({ReconciliationTemplate.session_id: target_scope}, synchronize_session=False)
        db.query(EntityAlias).filter(EntityAlias.scope_id == guest_scope).update({EntityAlias.scope_id: target_scope}, synchronize_session=False)
        db.query(MatchDecision).filter(MatchDecision.scope_id == guest_scope).update({MatchDecision.scope_id: target_scope}, synchronize_session=False)
        db.query(ResolutionRule).filter(ResolutionRule.scope_id == guest_scope).update({ResolutionRule.scope_id: target_scope}, synchronize_session=False)
        db.query(ExceptionPattern).filter(ExceptionPattern.scope_id == guest_scope).update({ExceptionPattern.scope_id: target_scope}, synchronize_session=False)
        for delivery in email_deliveries:
            delivery.scope_id = target_scope
            delivery.sender_user_id = user.id
        db.add(WorkspaceImport(source_scope=guest_scope, target_scope=target_scope, imported_by=user.id, counts_json=json.dumps(counts, sort_keys=True)))
        db.commit()
    except Exception:
        db.rollback()
        for path in copied_paths:
            try:
                storage.delete_file(path)
            except Exception:
                logger.exception("Could not remove staged guest import object")
        raise

    # The original immutable guest audit chain remains at its source scope;
    # an account event links to it instead of rewriting its hashes.
    record_event(
        scope_id=target_scope,
        actor=AuditActor("user", user.id),
        action="workspace.guest_imported",
        entity_type="workspace_import",
        entity_id=guest_scope,
        summary="Guest workspace imported into this account",
        after=counts,
        metadata={"source_scope": guest_scope},
    )
    record_event(
        scope_id=guest_scope,
        actor=AuditActor("user", user.id),
        action="workspace.imported_to_account",
        entity_type="workspace_import",
        entity_id=target_scope,
        summary="Guest workspace data was imported into a registered account",
        after=counts,
    )
    # Remove the old objects only after the ownership transaction commits.
    for old_path in old_paths:
        try:
            storage.delete_file(old_path)
        except Exception:
            logger.exception("Could not remove original guest import object")
    return {"message": "Guest workspace imported.", "counts": counts, "audit_source_scope": guest_scope}
