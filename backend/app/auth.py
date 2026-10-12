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

router = APIRouter(prefix="/api/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)

ISSUER = "frs-recovery-service"
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15
LOGIN_RATE_PER_MINUTE = 10
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ----------------------------------------------------------------------------- models
class RegisterRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: Optional[str] = Field(default=None, max_length=120)

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


# ----------------------------------------------------------------------------- helpers
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def public_user(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "full_name": u.get("full_name"), "role": u["role"],
            "created_at": u["created_at"], "last_login_at": u.get("last_login_at")}


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
    user = repo.create_user(body.email, hash_password(body.password), body.full_name, role="admin" if first_user else "operator")
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
    repo.record_login(user["id"], success=True)
    repo.audit(user["id"], "user.login", {})
    return issue_token(request, repo.get_user(user["id"]))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, user: dict = Depends(get_current_user)) -> Response:
    request.app.state.repo.revoke_auth_session(user["_jti"])
    request.app.state.repo.audit(user["id"], "user.logout", {})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me")
def me(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return public_user(user)
