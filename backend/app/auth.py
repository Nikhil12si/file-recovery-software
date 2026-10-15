"""
auth.py - Track C (Member 3), Module 1: User Authentication & Security.

JWT (HS256) bearer tokens with server-side session persistence:
  * every issued token carries a `jti` that is stored in auth_sessions,
  * logout revokes the jti, so a stolen token stops working immediately,
  * bcrypt (cost 12) password hashes, password policy, account lockout after
    5 failed logins (15 minutes) and a per-IP login rate limit,
  * the first registered account becomes `admin`; later ones are `operator`.

Endpoints (contracts/schemas/rest-api.schema.json):
    POST /api/auth/register   -> 201 auth_response
    POST /api/auth/login      -> 200 auth_response
    POST /api/auth/logout     -> 204
    GET  /api/auth/me         -> 200 user

The module depends only on `request.app.state.repo` (db.Repository) and
`request.app.state.settings`, keeping it decoupled from the recovery code.
"""
from __future__ import annotations

import collections
import datetime as dt
import re
import threading
import time
import uuid
from typing import Any, Optional

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from .twilio_service import generate_otp_code, mask_phone_number, send_sms_otp

router = APIRouter(prefix="/api/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)

ISSUER = "frs-recovery-service"
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15
LOGIN_RATE_PER_MINUTE = 10
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_pending_2fa: dict[str, dict] = {}


# ----------------------------------------------------------------------------- models
class RegisterRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: Optional[str] = Field(default=None, max_length=120)
    phone_number: Optional[str] = Field(default=None, max_length=32)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("invalid email address")
        return v

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
            raise ValueError("password must contain at least one letter and one digit")
        return v


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=128)
    phone_number: Optional[str] = Field(default=None, max_length=32)


class Verify2FARequest(BaseModel):
    verification_id: str = Field(..., max_length=128)
    code: str = Field(..., min_length=4, max_length=10)


class Resend2FARequest(BaseModel):
    verification_id: str = Field(..., max_length=128)
    phone_number: Optional[str] = Field(default=None, max_length=32)


# ----------------------------------------------------------------------------- helpers
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def public_user(u: dict) -> dict:
    return {
        "id": u["id"],
        "email": u["email"],
        "full_name": u.get("full_name"),
        "phone_number": u.get("phone_number"),
        "phone_masked": mask_phone_number(u.get("phone_number")),
        "role": u["role"],
        "created_at": u["created_at"],
        "last_login_at": u.get("last_login_at"),
    }


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def issue_token(request: Request, user: dict) -> dict:
    settings = request.app.state.settings
    now = dt.datetime.now(dt.timezone.utc)
    exp = now + dt.timedelta(minutes=settings.jwt_ttl_minutes)
    jti = uuid.uuid4().hex
    payload = {"sub": user["id"], "email": user["email"], "role": user["role"], "jti": jti, "iat": int(now.timestamp()),
               "exp": int(exp.timestamp()), "iss": ISSUER}
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    request.app.state.repo.create_auth_session(user["id"], jti, _iso(exp), request.headers.get("user-agent"),
                                               request.client.host if request.client else None)
    return {"access_token": token, "token_type": "bearer", "expires_at": _iso(exp), "user": public_user(user)}


class _RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.hits: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        self.lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many login attempts, try again in a minute.")
            q.append(now)


_login_limiter = _RateLimiter(LOGIN_RATE_PER_MINUTE)


# ----------------------------------------------------------------------------- dependencies
def get_current_user(request: Request, creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> dict:
    """FastAPI dependency: validates the bearer token and returns the user row."""
    token = creds.credentials if creds else request.query_params.get("access_token")  # query fallback for <img>/<a> downloads
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    settings = request.app.state.settings
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer=ISSUER, options={"require": ["exp", "sub", "jti"]})
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Session expired, please sign in again")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    repo = request.app.state.repo
    if not repo.auth_session_active(payload["jti"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Session was signed out")
    user = repo.get_user(payload["sub"])
    if not user or not user.get("is_active"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Account disabled")
    user["_jti"] = payload["jti"]
    return user


def require_role(*roles: str):
    def dep(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"Requires role: {', '.join(roles)}")
        return user

    return dep


# ----------------------------------------------------------------------------- routes
@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request) -> dict[str, Any]:
    settings, repo = request.app.state.settings, request.app.state.repo
    first_user = repo.count_users() == 0
    if not settings.allow_registration and not first_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Self-registration is disabled; ask an administrator.")
    if repo.get_user_by_email(body.email):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="An account with this email already exists")
    user = repo.create_user(
        body.email,
        hash_password(body.password),
        body.full_name,
        role="admin" if first_user else "operator",
        phone_number=body.phone_number,
    )
    repo.audit(user["id"], "user.register", {"email": user["email"], "role": user["role"]})
    return issue_token(request, user)


@router.post("/login")
def login(body: LoginRequest, request: Request) -> dict[str, Any]:
    repo = request.app.state.repo
    _login_limiter.check(request.client.host if request.client else "unknown")
    user = repo.get_user_by_email(body.email)
    generic = HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user:
        verify_password(body.password, "$2b$12$" + "x" * 53)  # constant-ish timing for unknown accounts
        raise generic
    if user.get("locked_until"):
        locked = dt.datetime.fromisoformat(str(user["locked_until"]).replace("Z", "+00:00"))
        if locked > dt.datetime.now(dt.timezone.utc):
            raise HTTPException(status.HTTP_423_LOCKED, detail=f"Account locked after repeated failures until {user['locked_until']}")
    if not user.get("is_active"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Account disabled")
    if not verify_password(body.password, user["password_hash"]):
        lock = None
        if int(user.get("failed_logins") or 0) + 1 >= MAX_FAILED_LOGINS:
            lock = _iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=LOCKOUT_MINUTES))
        repo.record_login(user["id"], success=False, lock_until=lock)
        repo.audit(user["id"], "user.login_failed", {})
        raise generic

    settings = request.app.state.settings
    phone = (body.phone_number or user.get("phone_number") or "").strip()

    # If 2FA is active, require Twilio OTP verification (using provided or registered phone)
    if settings.two_factor_enabled:
        target_phone = phone or "+919876543210"
        verification_id = uuid.uuid4().hex
        code = generate_otp_code(6)
        _pending_2fa[verification_id] = {
            "user_id": user["id"],
            "code": code,
            "phone": target_phone,
            "expires_at": time.time() + 300,
            "attempts": 0,
        }
        sms_res = send_sms_otp(
            to_phone=target_phone,
            code=code,
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            from_number=settings.twilio_phone_number,
        )
        dev_code = code if sms_res.get("simulated") else None
        return {
            "two_factor_required": True,
            "verification_id": verification_id,
            "phone_masked": mask_phone_number(target_phone),
            "dev_code": dev_code,
            "message": f"A 6-digit verification code was sent to {mask_phone_number(target_phone)} via Twilio SMS.",
        }

    repo.record_login(user["id"], success=True)
    repo.audit(user["id"], "user.login", {})
    return issue_token(request, repo.get_user(user["id"]))


@router.post("/verify-2fa")
def verify_2fa(body: Verify2FARequest, request: Request) -> dict[str, Any]:
    entry = _pending_2fa.get(body.verification_id)
    if not entry:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Verification session expired or invalid. Please sign in again.")
    if time.time() > entry["expires_at"]:
        _pending_2fa.pop(body.verification_id, None)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Verification code has expired. Please request a new code.")
    entry["attempts"] += 1
    if entry["attempts"] > 5:
        _pending_2fa.pop(body.verification_id, None)
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many invalid attempts. Please start over.")
    if body.code.strip() != entry["code"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid verification code. Please check your SMS and try again.")

    _pending_2fa.pop(body.verification_id, None)
    repo = request.app.state.repo
    user = repo.get_user(entry["user_id"])
    if not user or not user.get("is_active"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Account disabled")
    repo.record_login(user["id"], success=True)
    repo.audit(user["id"], "user.login_2fa", {})
    return issue_token(request, user)


@router.post("/resend-2fa")
def resend_2fa(body: Resend2FARequest, request: Request) -> dict[str, Any]:
    entry = _pending_2fa.get(body.verification_id)
    if not entry:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Session expired. Please log in again.")
    if body.phone_number and body.phone_number.strip():
        entry["phone"] = body.phone_number.strip()
    code = generate_otp_code(6)
    entry["code"] = code
    entry["expires_at"] = time.time() + 300
    entry["attempts"] = 0
    settings = request.app.state.settings
    sms_res = send_sms_otp(
        to_phone=entry["phone"],
        code=code,
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token,
        from_number=settings.twilio_phone_number,
    )
    dev_code = code if sms_res.get("simulated") else None
    return {
        "success": True,
        "phone_masked": mask_phone_number(entry["phone"]),
        "dev_code": dev_code,
        "message": f"Fresh verification code sent via Twilio SMS to {mask_phone_number(entry['phone'])}",
    }


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, user: dict = Depends(get_current_user)) -> Response:
    request.app.state.repo.revoke_auth_session(user["_jti"])
    request.app.state.repo.audit(user["id"], "user.logout", {})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me")
def me(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return public_user(user)
