"""Configuration for the public blog FastAPI service."""

from __future__ import annotations

from pydantic import BaseModel, Field

from blog_manager.config import BLOG_API_CONFIG


class BlogApiSettings(BaseModel):
    """Environment-backed settings for the blog reader API."""

    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60
    cors_origins: list[str] = Field(default_factory=list)
    mongo_uri: str = ""
    mongo_username: str = ""
    mongo_password: str = ""
    mongo_auth_mechanism: str = "SCRAM-SHA-256"
    mongo_require_auth: bool = True
    mongo_auth_source: str = "admin"
    mongo_database: str = "entourage_blog"
    users_collection: str = "blog_users"
    comments_collection: str = "blog_comments"
    subscribers_collection: str = "blog_subscribers"
    email_tokens_collection: str = "blog_email_tokens"
    digest_sends_collection: str = "blog_digest_sends"
    api_base_url: str = ""
    ses_sender_email: str = ""
    ses_configuration_set: str = ""
    moderation_mode: str = "manual_v1"

    @classmethod
    def from_env(cls) -> "BlogApiSettings":
        return cls(
            jwt_secret=BLOG_API_CONFIG["JWT_SECRET"],
            jwt_algorithm=BLOG_API_CONFIG["JWT_ALGORITHM"],
            access_token_ttl_minutes=BLOG_API_CONFIG["ACCESS_TOKEN_TTL_MINUTES"],
            cors_origins=BLOG_API_CONFIG["CORS_ORIGINS"],
            mongo_uri=BLOG_API_CONFIG["MONGODB_URI"],
            mongo_username=BLOG_API_CONFIG["MONGODB_USERNAME"],
            mongo_password=BLOG_API_CONFIG["MONGODB_PASSWORD"],
            mongo_auth_mechanism=BLOG_API_CONFIG["MONGODB_AUTH_MECHANISM"],
            mongo_require_auth=BLOG_API_CONFIG["MONGODB_REQUIRE_AUTH"],
            mongo_auth_source=BLOG_API_CONFIG["MONGODB_AUTH_SOURCE"],
            mongo_database=BLOG_API_CONFIG["MONGODB_DATABASE"],
            users_collection=BLOG_API_CONFIG["USERS_COLLECTION"],
            comments_collection=BLOG_API_CONFIG["COMMENTS_COLLECTION"],
            subscribers_collection=BLOG_API_CONFIG["SUBSCRIBERS_COLLECTION"],
            email_tokens_collection=BLOG_API_CONFIG["EMAIL_TOKENS_COLLECTION"],
            digest_sends_collection=BLOG_API_CONFIG["DIGEST_SENDS_COLLECTION"],
            api_base_url=BLOG_API_CONFIG["API_BASE_URL"],
            ses_sender_email=BLOG_API_CONFIG["SES_SENDER_EMAIL"],
            ses_configuration_set=BLOG_API_CONFIG["SES_CONFIGURATION_SET"],
            moderation_mode=BLOG_API_CONFIG["MODERATION_MODE"],
        )
