"""Internal data models for the blog reader API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid4().hex


@dataclass(frozen=True)
class BlogUser:
    id: str
    username: str
    email: str
    password_hash: str
    role: str = "reader"
    email_verified: bool = False
    status: str = "active"
    approved_comment_count: int = 0
    recent_rejection_count: int = 0
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class BlogComment:
    id: str
    post_slug: str
    author_user_id: str
    author_username: str
    body: str
    status: str
    moderation_reason: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BlogSubscriber:
    id: str
    email: str
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BlogEmailToken:
    token: str
    email: str
    purpose: str
    created_at: datetime
    used_at: datetime | None = None
