"""
Auth Service — adapted from finpilot-ai auth.py
Same endpoint contracts (register, login, refresh, logout, me) but backed
by an in-memory user store so no DB / Redis is required for the prototype.

JWT structure mirrors finpilot:
  { "sub": user_id, "type": "access"|"refresh", "jti": uuid, "exp": ... }
"""
from __future__ import annotations

import uuid
import hashlib
import hmac
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr

# ── Config ─────────────────────────────────────────────────────────────────────
_jwt_secret = os.environ.get("JWT_SECRET")
if not _jwt_secret:
    raise RuntimeError(
        "JWT_SECRET environment variable is not set. "
        "Set a strong random secret before starting the application."
    )
SECRET_KEY      = _jwt_secret
ACCESS_EXPIRE   = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", 60))
REFRESH_EXPIRE  = int(os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS",   7))

# ── Database store (SQLite via db.py) ──────────────────────────────────────────
from db import (
    get_user_by_email,
    get_user_by_id,
    save_user,
    update_user_last_login,
    is_jti_blacklisted,
    blacklist_jti,
)

# ── Pydantic schemas (mirrors finpilot schemas/user.py) ───────────────────────
class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    phone_number: Optional[str] = None

class UserResponse(BaseModel):
    id: str
    email: EmailStr
    full_name: Optional[str]
    is_active: bool
    is_verified: bool
    created_at: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class RefreshToken(BaseModel):
    refresh_token: str

# ── Minimal JWT (no PyJWT dependency — uses HMAC-SHA256) ──────────────────────
import base64, json as _json

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64url_decode(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (pad % 4))

def _create_token(payload: Dict[str, Any]) -> str:
    header  = _b64url_encode(_json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body    = _b64url_encode(_json.dumps(payload).encode())
    sig_input = f"{header}.{body}".encode()
    sig     = hmac.new(SECRET_KEY.encode(), sig_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64url_encode(sig)}"

def _decode_token(token: str) -> Dict[str, Any]:
    try:
        header, body, sig = token.split(".")
        expected = hmac.new(SECRET_KEY.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64url_decode(sig), expected):
            raise ValueError("bad signature")
        payload = _json.loads(_b64url_decode(body))
        if payload.get("exp", 0) < time.time():
            raise ValueError("token expired")
        return payload
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}")

def create_access_token(user_id: str) -> str:
    return _create_token({
        "sub":  user_id,
        "type": "access",
        "jti":  str(uuid.uuid4()),
        "iat":  int(time.time()),
        "exp":  int(time.time()) + ACCESS_EXPIRE * 60,
    })

def create_refresh_token(user_id: str) -> str:
    return _create_token({
        "sub":  user_id,
        "type": "refresh",
        "jti":  str(uuid.uuid4()),
        "iat":  int(time.time()),
        "exp":  int(time.time()) + REFRESH_EXPIRE * 86400,
    })

# ── Password hashing (bcrypt) ────────────────────────────────────────────────
def _hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

# ── OAuth2 bearer ──────────────────────────────────────────────────────────────
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

def get_current_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = _decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Wrong token type")
    if is_jti_blacklisted(payload.get("jti", "")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")
    user_id = payload.get("sub")
    user = get_user_by_id(user_id)
    if not user or not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user

# ── Router ─────────────────────────────────────────────────────────────────────
router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user_data: UserCreate) -> Any:
    """
    Register a new user.
    - email must be unique
    - password min 6 chars
    """
    existing = get_user_by_email(user_data.email)
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    if len(user_data.password) < 6:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 6 characters")

    user_id = str(uuid.uuid4())
    user = {
        "id":           user_id,
        "email":        user_data.email,
        "full_name":    user_data.full_name,
        "phone_number": user_data.phone_number,
        "hashed_password": _hash_password(user_data.password),
        "is_active":    True,
        "is_verified":  True,          # auto-verify in prototype
        "created_at":   datetime.now(timezone.utc).isoformat(),
        "last_login":   None,
    }
    save_user(user)
    return UserResponse(**{k: v for k, v in user.items() if k in UserResponse.model_fields})


@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends()) -> Any:
    """
    Login with email + password (OAuth2 form).
    Returns access_token and refresh_token.
    """
    user = get_user_by_email(form_data.username)   # username field holds email
    if not user or not _verify_password(form_data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is inactive")

    now = datetime.now(timezone.utc).isoformat()
    update_user_last_login(user["email"], now)
    return Token(
        access_token=create_access_token(user["id"]),
        refresh_token=create_refresh_token(user["id"]),
    )


@router.post("/refresh", response_model=Token)
def refresh_token(body: RefreshToken) -> Any:
    """
    Exchange a valid refresh token for a new access + refresh token pair.
    """
    payload = _decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Wrong token type")
    if is_jti_blacklisted(payload.get("jti", "")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")

    user_id = payload.get("sub")
    user = get_user_by_id(user_id)
    if not user or not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    # Revoke old refresh token
    blacklist_jti(payload["jti"], datetime.now(timezone.utc).isoformat())

    return Token(
        access_token=create_access_token(user["id"]),
        refresh_token=create_refresh_token(user["id"]),
    )


@router.post("/logout")
def logout(token: str = Depends(oauth2_scheme)) -> Any:
    """
    Logout — blacklists the current access token's JTI immediately.
    """
    if token:
        try:
            payload = _decode_token(token)
            blacklist_jti(payload["jti"], datetime.now(timezone.utc).isoformat())
        except Exception:
            pass  # Already invalid — still return success
    return {"message": "Successfully logged out"}


@router.get("/me", response_model=UserResponse)
def me(current_user: Dict = Depends(get_current_user)) -> Any:
    """Return the currently authenticated user's profile."""
    return UserResponse(**{k: v for k, v in current_user.items() if k in UserResponse.model_fields})
