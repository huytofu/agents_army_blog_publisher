"""Storage repositories for blog API data."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from pymongo import ReturnDocument
from pymongo.database import Database

from blog_manager.api.config import BlogApiSettings
from blog_manager.api.models import BlogComment, BlogEmailToken, BlogSubscriber, BlogUser, new_id, utc_now
from blog_manager.api.mongo_schemas import (
    BlogCommentDocument,
    BlogDigestSendDocument,
    BlogEmailTokenDocument,
    BlogSubscriberDocument,
    BlogUserDocument,
    comment_document_from_model,
    email_token_document_from_model,
    subscriber_document_from_model,
    user_document_from_model,
    validate_update_fields,
)
from blog_manager.api.mongo_security import (
    build_safe_eq_query,
    build_safe_inc_update,
    build_safe_set_on_insert_update,
    build_safe_set_update,
    merge_update_operators,
    sanitize_text,
)


class BlogRepository(Protocol):
    def create_user(
        self,
        *,
        username: str,
        email: str,
        password_hash: str,
        role: str = "reader",
        email_verified: bool = False,
    ) -> BlogUser:
        ...

    def find_user_by_username(self, username: str) -> BlogUser | None:
        ...

    def find_user_by_id(self, user_id: str) -> BlogUser | None:
        ...

    def find_user_by_email(self, email: str) -> BlogUser | None:
        ...

    def mark_user_email_verified(self, email: str) -> BlogUser | None:
        ...

    def create_email_token(self, *, email: str, purpose: str) -> BlogEmailToken:
        ...

    def consume_email_token(self, *, token: str, purpose: str) -> BlogEmailToken | None:
        ...

    def create_comment(
        self,
        *,
        post_slug: str,
        author: BlogUser,
        body: str,
        status: str,
        moderation_reason: str,
    ) -> BlogComment:
        ...

    def list_approved_comments(self, post_slug: str) -> list[BlogComment]:
        ...

    def update_comment_status(self, *, comment_id: str, status: str, reason: str) -> BlogComment | None:
        ...

    def upsert_subscriber(self, *, email: str) -> BlogSubscriber:
        ...

    def update_subscriber_status(self, *, email: str, status: str) -> BlogSubscriber | None:
        ...

    def list_confirmed_subscribers(self) -> list[BlogSubscriber]:
        ...

    def has_digest_send(self, *, email: str, highlight_slug: str) -> bool:
        ...

    def record_digest_send(self, *, email: str, highlight_slug: str) -> None:
        ...


class InMemoryBlogRepository:
    """Small real repository used by tests and local app injection."""

    def __init__(self) -> None:
        self.users: dict[str, BlogUser] = {}
        self.comments: dict[str, BlogComment] = {}
        self.subscribers: dict[str, BlogSubscriber] = {}
        self.email_tokens: dict[str, BlogEmailToken] = {}
        self.digest_sends: set[tuple[str, str]] = set()

    def create_user(
        self,
        *,
        username: str,
        email: str,
        password_hash: str,
        role: str = "reader",
        email_verified: bool = False,
    ) -> BlogUser:
        normalized_username = username.strip().casefold()
        normalized_email = _normalize_email(email)
        if self.find_user_by_username(normalized_username) or self.find_user_by_email(normalized_email):
            raise ValueError("Blog user already exists.")
        user = BlogUser(
            id=new_id(),
            username=normalized_username,
            email=normalized_email,
            password_hash=password_hash,
            role=role,
            email_verified=email_verified,
        )
        self.users[user.id] = user
        return user

    def find_user_by_username(self, username: str) -> BlogUser | None:
        normalized = username.strip().casefold()
        return next((user for user in self.users.values() if user.username == normalized), None)

    def find_user_by_id(self, user_id: str) -> BlogUser | None:
        return self.users.get(user_id)

    def find_user_by_email(self, email: str) -> BlogUser | None:
        normalized = _normalize_email(email)
        return next((user for user in self.users.values() if user.email == normalized), None)

    def mark_user_email_verified(self, email: str) -> BlogUser | None:
        user = self.find_user_by_email(email)
        if user is None:
            return None
        updated = replace(user, email_verified=True)
        self.users[updated.id] = updated
        return updated

    def create_email_token(self, *, email: str, purpose: str) -> BlogEmailToken:
        token = BlogEmailToken(
            token=new_id(),
            email=_normalize_email(email),
            purpose=purpose,
            created_at=utc_now(),
        )
        self.email_tokens[token.token] = token
        return token

    def consume_email_token(self, *, token: str, purpose: str) -> BlogEmailToken | None:
        item = self.email_tokens.get(token)
        if item is None or item.purpose != purpose or item.used_at is not None:
            return None
        used = replace(item, used_at=utc_now())
        self.email_tokens[token] = used
        return used

    def latest_email_token(self, *, email: str, purpose: str) -> str:
        normalized = _normalize_email(email)
        matches = [
            item for item in self.email_tokens.values()
            if item.email == normalized and item.purpose == purpose
        ]
        if not matches:
            raise AssertionError(f"No token found for {normalized} purpose={purpose}.")
        return sorted(matches, key=lambda item: item.created_at)[-1].token

    def create_comment(
        self,
        *,
        post_slug: str,
        author: BlogUser,
        body: str,
        status: str,
        moderation_reason: str,
    ) -> BlogComment:
        now = utc_now()
        comment = BlogComment(
            id=new_id(),
            post_slug=post_slug,
            author_user_id=author.id,
            author_username=author.username,
            body=body.strip(),
            status=status,
            moderation_reason=moderation_reason,
            created_at=now,
            updated_at=now,
        )
        self.comments[comment.id] = comment
        if status == "approved":
            self.users[author.id] = replace(
                author,
                approved_comment_count=author.approved_comment_count + 1,
            )
        return comment

    def list_approved_comments(self, post_slug: str) -> list[BlogComment]:
        comments = [
            comment for comment in self.comments.values()
            if comment.post_slug == post_slug and comment.status == "approved"
        ]
        return sorted(comments, key=lambda comment: comment.created_at)

    def update_comment_status(self, *, comment_id: str, status: str, reason: str) -> BlogComment | None:
        comment = self.comments.get(comment_id)
        if comment is None:
            return None
        updated = replace(comment, status=status, moderation_reason=reason, updated_at=utc_now())
        self.comments[comment_id] = updated
        author = self.users.get(updated.author_user_id)
        if author and status == "approved" and comment.status != "approved":
            self.users[author.id] = replace(
                author,
                approved_comment_count=author.approved_comment_count + 1,
            )
        return updated

    def upsert_subscriber(self, *, email: str) -> BlogSubscriber:
        normalized = _normalize_email(email)
        existing = self.subscribers.get(normalized)
        if existing:
            updated = replace(existing, status="pending", updated_at=utc_now())
            self.subscribers[normalized] = updated
            return updated
        now = utc_now()
        subscriber = BlogSubscriber(
            id=new_id(),
            email=normalized,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        self.subscribers[normalized] = subscriber
        return subscriber

    def update_subscriber_status(self, *, email: str, status: str) -> BlogSubscriber | None:
        normalized = _normalize_email(email)
        subscriber = self.subscribers.get(normalized)
        if subscriber is None:
            return None
        updated = replace(subscriber, status=status, updated_at=utc_now())
        self.subscribers[normalized] = updated
        return updated

    def list_confirmed_subscribers(self) -> list[BlogSubscriber]:
        subscribers = [
            subscriber for subscriber in self.subscribers.values()
            if subscriber.status == "confirmed"
        ]
        return sorted(subscribers, key=lambda subscriber: subscriber.email)

    def has_digest_send(self, *, email: str, highlight_slug: str) -> bool:
        return (_normalize_email(email), highlight_slug) in self.digest_sends

    def record_digest_send(self, *, email: str, highlight_slug: str) -> None:
        self.digest_sends.add((_normalize_email(email), highlight_slug))


class MongoBlogRepository:
    """MongoDB Atlas-backed repository for production Lambda use."""

    def __init__(self, database: Database, settings: BlogApiSettings):
        self.settings = settings
        self.users = database[settings.users_collection]
        self.comments = database[settings.comments_collection]
        self.subscribers = database[settings.subscribers_collection]
        self.email_tokens = database[settings.email_tokens_collection]
        self.digest_sends = database[settings.digest_sends_collection]

    def create_user(
        self,
        *,
        username: str,
        email: str,
        password_hash: str,
        role: str = "reader",
        email_verified: bool = False,
    ) -> BlogUser:
        user = BlogUser(
            id=new_id(),
            username=username.strip().casefold(),
            email=_normalize_email(email),
            password_hash=password_hash,
            role=role,
            email_verified=email_verified,
        )
        self.users.insert_one(user_document_from_model(user).to_mongo_document())
        return user

    def find_user_by_username(self, username: str) -> BlogUser | None:
        return _user_from_document(
            self.users.find_one(build_safe_eq_query("username", username.strip().casefold()))
        )

    def find_user_by_id(self, user_id: str) -> BlogUser | None:
        return _user_from_document(self.users.find_one(build_safe_eq_query("id", user_id)))

    def find_user_by_email(self, email: str) -> BlogUser | None:
        return _user_from_document(self.users.find_one(build_safe_eq_query("email", _normalize_email(email))))

    def mark_user_email_verified(self, email: str) -> BlogUser | None:
        result = self.users.find_one_and_update(
            build_safe_eq_query("email", _normalize_email(email)),
            build_safe_set_update(
                validate_update_fields(BlogUserDocument, {"email_verified": True})
            ),
            return_document=ReturnDocument.AFTER,
        )
        return _user_from_document(result)

    def create_email_token(self, *, email: str, purpose: str) -> BlogEmailToken:
        token = BlogEmailToken(token=new_id(), email=_normalize_email(email), purpose=purpose, created_at=utc_now())
        self.email_tokens.insert_one(email_token_document_from_model(token).to_mongo_document())
        return token

    def consume_email_token(self, *, token: str, purpose: str) -> BlogEmailToken | None:
        result = self.email_tokens.find_one_and_update(
            {
                **build_safe_eq_query("token", token),
                **build_safe_eq_query("purpose", purpose),
                **build_safe_eq_query("used_at", None),
            },
            build_safe_set_update(
                validate_update_fields(BlogEmailTokenDocument, {"used_at": utc_now()})
            ),
            return_document=ReturnDocument.AFTER,
        )
        return _email_token_from_document(result)

    def create_comment(
        self,
        *,
        post_slug: str,
        author: BlogUser,
        body: str,
        status: str,
        moderation_reason: str,
    ) -> BlogComment:
        now = utc_now()
        comment = BlogComment(
            id=new_id(),
            post_slug=sanitize_text(post_slug.strip().casefold(), max_length=120),
            author_user_id=author.id,
            author_username=author.username,
            body=sanitize_text(body.strip(), max_length=2000),
            status=status,
            moderation_reason=moderation_reason,
            created_at=now,
            updated_at=now,
        )
        self.comments.insert_one(comment_document_from_model(comment).to_mongo_document())
        if status == "approved":
            self.users.update_one(
                build_safe_eq_query("id", author.id),
                build_safe_inc_update({"approved_comment_count": 1}),
            )
        return comment

    def list_approved_comments(self, post_slug: str) -> list[BlogComment]:
        cursor = self.comments.find(
            {
                **build_safe_eq_query("post_slug", post_slug),
                **build_safe_eq_query("status", "approved"),
            }
        ).sort("created_at", 1)
        return [_comment_from_document(item) for item in cursor]

    def update_comment_status(self, *, comment_id: str, status: str, reason: str) -> BlogComment | None:
        existing = _comment_from_document(self.comments.find_one(build_safe_eq_query("id", comment_id)))
        if existing is None:
            return None
        result = self.comments.find_one_and_update(
            build_safe_eq_query("id", comment_id),
            build_safe_set_update(
                validate_update_fields(
                    BlogCommentDocument,
                    {"status": status, "moderation_reason": reason, "updated_at": utc_now()},
                )
            ),
            return_document=ReturnDocument.AFTER,
        )
        updated = _comment_from_document(result)
        if updated and status == "approved" and existing.status != "approved":
            self.users.update_one(
                build_safe_eq_query("id", updated.author_user_id),
                build_safe_inc_update({"approved_comment_count": 1}),
            )
        return updated

    def upsert_subscriber(self, *, email: str) -> BlogSubscriber:
        normalized = _normalize_email(email)
        now = utc_now()
        insert_doc = BlogSubscriberDocument(
            id=new_id(),
            email=normalized,
            status="pending",
            created_at=now,
            updated_at=now,
        ).model_dump()
        self.subscribers.update_one(
            build_safe_eq_query("email", normalized),
            merge_update_operators(
                build_safe_set_update(
                    validate_update_fields(
                        BlogSubscriberDocument,
                        {"status": "pending", "updated_at": now},
                    )
                ),
                build_safe_set_on_insert_update(
                    {
                        "id": insert_doc["id"],
                        "email": insert_doc["email"],
                        "created_at": insert_doc["created_at"],
                    }
                ),
            ),
            upsert=True,
        )
        return _subscriber_from_document(self.subscribers.find_one(build_safe_eq_query("email", normalized)))

    def update_subscriber_status(self, *, email: str, status: str) -> BlogSubscriber | None:
        result = self.subscribers.find_one_and_update(
            build_safe_eq_query("email", _normalize_email(email)),
            build_safe_set_update(
                validate_update_fields(
                    BlogSubscriberDocument,
                    {"status": status, "updated_at": utc_now()},
                )
            ),
            return_document=ReturnDocument.AFTER,
        )
        return _subscriber_from_document(result)

    def list_confirmed_subscribers(self) -> list[BlogSubscriber]:
        cursor = self.subscribers.find({"status": "confirmed"}).sort("email", 1)
        return [_subscriber_from_document(item) for item in cursor]

    def has_digest_send(self, *, email: str, highlight_slug: str) -> bool:
        return self.digest_sends.find_one(
            {
                **build_safe_eq_query("email", _normalize_email(email)),
                **build_safe_eq_query("highlight_slug", highlight_slug),
            }
        ) is not None

    def record_digest_send(self, *, email: str, highlight_slug: str) -> None:
        digest_send = BlogDigestSendDocument(
            id=new_id(),
            email=_normalize_email(email),
            highlight_slug=highlight_slug,
            created_at=utc_now(),
        ).model_dump()
        self.digest_sends.update_one(
            {
                **build_safe_eq_query("email", digest_send["email"]),
                **build_safe_eq_query("highlight_slug", digest_send["highlight_slug"]),
            },
            build_safe_set_on_insert_update(digest_send),
            upsert=True,
        )


def _normalize_email(email: str) -> str:
    return email.strip().casefold()


def _user_from_document(document: dict | None) -> BlogUser | None:
    if not document:
        return None
    return BlogUser(**BlogUserDocument.model_validate(_clean_document(document)).model_dump())


def _comment_from_document(document: dict | None) -> BlogComment | None:
    if not document:
        return None
    return BlogComment(**BlogCommentDocument.model_validate(_clean_document(document)).model_dump())


def _subscriber_from_document(document: dict | None) -> BlogSubscriber | None:
    if not document:
        return None
    return BlogSubscriber(**BlogSubscriberDocument.model_validate(_clean_document(document)).model_dump())


def _email_token_from_document(document: dict | None) -> BlogEmailToken | None:
    if not document:
        return None
    return BlogEmailToken(**BlogEmailTokenDocument.model_validate(_clean_document(document)).model_dump())


def _clean_document(document: dict) -> dict:
    return {key: value for key, value in document.items() if key != "_id"}
