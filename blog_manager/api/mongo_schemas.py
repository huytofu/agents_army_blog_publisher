"""MongoDB collection schemas for the blog API.

These schemas describe stored MongoDB documents. They are separate from API
request/response models so repository validation can evolve without changing
public route contracts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from blog_manager.api.models import BlogComment, BlogEmailToken, BlogSubscriber, BlogUser, utc_now
from blog_manager.api.mongo_security import sanitize_text

class MongoDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    def to_mongo_document(self) -> dict[str, Any]:
        document = self.model_dump()
        document["_id"] = document.get("id") or document.get("token")
        return document


class BlogUserDocument(MongoDocument):
    id: str = Field(description="Stable blog-only user identifier.")
    username: str = Field(description="Normalized public blog username.", min_length=3, max_length=32)
    email: EmailStr = Field(description="Normalized blog account email address.")
    password_hash: str = Field(description="Bcrypt password hash. Never expose in API responses.")
    role: Literal["reader", "admin"] = Field(default="reader", description="Blog API authorization role.")
    email_verified: bool = Field(default=False, description="Whether the user completed email verification.")
    status: Literal["active", "disabled"] = Field(default="active", description="Account lifecycle status.")
    approved_comment_count: int = Field(default=0, ge=0, description="Number of approved comments by this user.")
    recent_rejection_count: int = Field(default=0, ge=0, description="Recent rejected-comment count for moderation.")
    created_at: datetime = Field(default_factory=utc_now, description="UTC creation timestamp.")

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        username = sanitize_text(str(value).strip().casefold(), max_length=32)
        if not username.replace("_", "").isalnum():
            raise ValueError("Username must contain only letters, numbers, and underscores.")
        return username

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return sanitize_text(str(value).strip().casefold(), max_length=254)


class BlogCommentDocument(MongoDocument):
    id: str = Field(description="Stable comment identifier.")
    post_slug: str = Field(
        description="Generated blog post slug that owns this comment.",
        json_schema_extra={"foreign_key": "blog/posts.json.slug"},
    )
    author_user_id: str = Field(
        description="Blog user id for the comment author.",
        json_schema_extra={"foreign_key": "blog_users.id"},
    )
    author_username: str = Field(description="Denormalized public author username for rendering.")
    body: str = Field(description="Plain-text comment body.", min_length=1, max_length=2000)
    status: Literal["pending", "approved", "rejected"] = Field(description="Moderation status.")
    moderation_reason: str = Field(description="Deterministic moderation/admin reason code.")
    created_at: datetime = Field(default_factory=utc_now, description="UTC creation timestamp.")
    updated_at: datetime = Field(default_factory=utc_now, description="UTC last update timestamp.")

    @field_validator("post_slug", "author_username", mode="before")
    @classmethod
    def normalize_short_text(cls, value: str) -> str:
        return sanitize_text(str(value).strip().casefold(), max_length=100)

    @field_validator("body", mode="before")
    @classmethod
    def validate_body(cls, value: str) -> str:
        return sanitize_text(str(value).strip(), max_length=2000)


class BlogSubscriberDocument(MongoDocument):
    id: str = Field(description="Stable subscriber identifier.")
    email: EmailStr = Field(description="Normalized subscriber email address.")
    status: Literal["pending", "confirmed", "unsubscribed"] = Field(description="Subscriber lifecycle status.")
    created_at: datetime = Field(default_factory=utc_now, description="UTC creation timestamp.")
    updated_at: datetime = Field(default_factory=utc_now, description="UTC last update timestamp.")

    @field_validator("email", mode="before")
    @classmethod
    def normalize_subscriber_email(cls, value: str) -> str:
        return sanitize_text(str(value).strip().casefold(), max_length=254)


class BlogEmailTokenDocument(MongoDocument):
    token: str = Field(description="Opaque one-time token value.")
    email: EmailStr = Field(description="Normalized email address this token belongs to.")
    purpose: Literal["verify_email", "confirm_subscription", "unsubscribe"] = Field(
        description="Token purpose; must match the consuming endpoint."
    )
    created_at: datetime = Field(default_factory=utc_now, description="UTC creation timestamp.")
    used_at: datetime | None = Field(default=None, description="UTC timestamp when token was consumed.")

    @field_validator("email", mode="before")
    @classmethod
    def normalize_token_email(cls, value: str) -> str:
        return sanitize_text(str(value).strip().casefold(), max_length=254)


class BlogDigestSendDocument(MongoDocument):
    id: str = Field(description="Stable digest-send identifier.")
    email: EmailStr = Field(
        description="Confirmed subscriber email address.",
        json_schema_extra={"foreign_key": "blog_subscribers.email"},
    )
    highlight_slug: str = Field(
        description="Weekly highlighted post slug.",
        json_schema_extra={"foreign_key": "weekly_highlight.json.slug"},
    )
    created_at: datetime = Field(default_factory=utc_now, description="UTC timestamp when this send was recorded.")

    @field_validator("email", mode="before")
    @classmethod
    def normalize_digest_email(cls, value: str) -> str:
        return sanitize_text(str(value).strip().casefold(), max_length=254)

    @field_validator("highlight_slug", mode="before")
    @classmethod
    def normalize_highlight_slug(cls, value: str) -> str:
        return sanitize_text(str(value).strip().casefold(), max_length=120)


def user_document_from_model(user: BlogUser) -> BlogUserDocument:
    return BlogUserDocument(**user.__dict__)


def comment_document_from_model(comment: BlogComment) -> BlogCommentDocument:
    return BlogCommentDocument(**comment.__dict__)


def subscriber_document_from_model(subscriber: BlogSubscriber) -> BlogSubscriberDocument:
    return BlogSubscriberDocument(**subscriber.__dict__)


def email_token_document_from_model(token: BlogEmailToken) -> BlogEmailTokenDocument:
    return BlogEmailTokenDocument(**token.__dict__)


def validate_update_fields(schema: type[BaseModel], updates: dict[str, Any]) -> dict[str, Any]:
    """Validate update values against known schema fields before Mongo writes."""
    validated: dict[str, Any] = {}
    for field_name, value in updates.items():
        if field_name not in schema.model_fields:
            raise ValueError(f"Unknown Mongo update field: {field_name}")
        validated[field_name] = _validate_update_value(schema, field_name, value)
    return validated


def _validate_update_value(schema: type[BaseModel], field_name: str, value: Any) -> Any:
    if schema is BlogUserDocument and field_name == "email_verified":
        if not isinstance(value, bool):
            raise ValueError("email_verified must be a boolean.")
        return value
    if schema is BlogUserDocument and field_name in {"approved_comment_count", "recent_rejection_count"}:
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"{field_name} must be a non-negative integer.")
        return value
    if schema is BlogCommentDocument and field_name == "status":
        if value not in {"pending", "approved", "rejected"}:
            raise ValueError("Invalid comment status.")
        return value
    if schema is BlogSubscriberDocument and field_name == "status":
        if value not in {"pending", "confirmed", "unsubscribed"}:
            raise ValueError("Invalid subscriber status.")
        return value
    if field_name.endswith("_at"):
        if value is not None and not isinstance(value, datetime):
            raise ValueError(f"{field_name} must be a datetime or None.")
        return value
    if isinstance(value, str):
        max_length = 2000 if field_name == "body" else 500
        return sanitize_text(value.strip(), max_length=max_length)
    if isinstance(value, bool) or isinstance(value, int) or value is None:
        return value
    raise ValueError(f"Unsupported update value for {field_name}.")
