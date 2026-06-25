"""MongoDB Atlas connection helpers for the blog API."""

from __future__ import annotations

from functools import lru_cache

from pymongo import ASCENDING, MongoClient
from pymongo.database import Database

from blog_manager.api.config import BlogApiSettings
from blog_manager.api.repositories import MongoBlogRepository


@lru_cache(maxsize=1)
def get_mongo_client(mongo_uri: str) -> MongoClient:
    if not mongo_uri.strip():
        raise RuntimeError("BLOG_API_MONGODB_URI is required for the blog API.")
    return MongoClient(mongo_uri)


def get_database(settings: BlogApiSettings) -> Database:
    return get_mongo_client(settings.mongo_uri)[settings.mongo_database]


def build_mongo_repository(settings: BlogApiSettings) -> MongoBlogRepository:
    database = get_database(settings)
    ensure_indexes(database, settings)
    return MongoBlogRepository(database, settings)


def ensure_indexes(database: Database, settings: BlogApiSettings) -> None:
    users = database[settings.users_collection]
    users.create_index([("username", ASCENDING)], unique=True)
    users.create_index([("email", ASCENDING)], unique=True)

    comments = database[settings.comments_collection]
    comments.create_index([("post_slug", ASCENDING), ("status", ASCENDING), ("created_at", ASCENDING)])
    comments.create_index([("author_user_id", ASCENDING), ("created_at", ASCENDING)])

    subscribers = database[settings.subscribers_collection]
    subscribers.create_index([("email", ASCENDING)], unique=True)
    subscribers.create_index([("status", ASCENDING)])

    email_tokens = database[settings.email_tokens_collection]
    email_tokens.create_index([("token", ASCENDING)], unique=True)
    email_tokens.create_index([("email", ASCENDING), ("purpose", ASCENDING), ("created_at", ASCENDING)])

    database[settings.digest_sends_collection].create_index(
        [("email", ASCENDING), ("highlight_slug", ASCENDING)],
        unique=True,
    )
