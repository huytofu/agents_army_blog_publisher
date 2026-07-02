"""MongoDB Atlas connection helpers for the blog API."""

from __future__ import annotations

import urllib.parse
from functools import lru_cache

import certifi
from pymongo import ASCENDING, MongoClient
from pymongo.database import Database

from blog_manager.api.config import BlogApiSettings
from blog_manager.api.repositories import MongoBlogRepository

_MONGO_CLIENT_KWARGS = {
    "tls": True,
    "tlsCAFile": certifi.where(),
    "tlsAllowInvalidCertificates": False,
    "tlsAllowInvalidHostnames": False,
    "maxPoolSize": 50,
    "minPoolSize": 5,
    "maxIdleTimeMS": 30000,
    "serverSelectionTimeoutMS": 5000,
    "connectTimeoutMS": 10000,
    "socketTimeoutMS": 20000,
    "retryWrites": True,
    "retryReads": True,
}


@lru_cache(maxsize=1)
def get_mongo_client(
    connection_string: str,
    username: str,
    password: str,
    require_auth: bool,
    auth_mechanism: str,
    auth_source: str,
) -> MongoClient:
    if not connection_string.strip():
        raise RuntimeError("BLOG_API_MONGODB_URI is required for the blog API.")

    if (
        connection_string.startswith("mongodb+srv://")
        and username
        and password
        and require_auth
    ):
        encoded_username = urllib.parse.quote_plus(username)
        encoded_password = urllib.parse.quote_plus(password)
        uri_parts = connection_string.replace("mongodb+srv://", "")
        connection_uri = f"mongodb+srv://{encoded_username}:{encoded_password}{uri_parts}"
        return MongoClient(connection_uri, **_MONGO_CLIENT_KWARGS)

    connection_params: dict = {"host": connection_string}
    if username and password and require_auth:
        connection_params["username"] = username
        connection_params["password"] = password
        connection_params["authSource"] = auth_source
        connection_params["authMechanism"] = auth_mechanism

    return MongoClient(**connection_params, **_MONGO_CLIENT_KWARGS)


def get_database(settings: BlogApiSettings) -> Database:
    client = get_mongo_client(
        settings.mongo_uri,
        settings.mongo_username,
        settings.mongo_password,
        settings.mongo_require_auth,
        settings.mongo_auth_mechanism,
        settings.mongo_auth_source,
    )
    return client[settings.mongo_database]


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
