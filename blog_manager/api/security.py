"""Security helpers for blog-only reader accounts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from blog_manager.api.config import BlogApiSettings


class BlogAuthError(RuntimeError):
    """Raised when an auth token or credential is invalid."""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(*, user_id: str, settings: BlogApiSettings) -> str:
    secret = _require_secret(settings)
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, *, settings: BlogApiSettings) -> str:
    secret = _require_secret(settings)
    try:
        payload: dict[str, Any] = jwt.decode(token, secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise BlogAuthError("Invalid access token.") from exc
    if payload.get("type") != "access" or not payload.get("sub"):
        raise BlogAuthError("Invalid access token.")
    return str(payload["sub"])


def _require_secret(settings: BlogApiSettings) -> str:
    secret = settings.jwt_secret.strip()
    if not secret:
        raise BlogAuthError("BLOG_API_JWT_SECRET is required.")
    return secret
