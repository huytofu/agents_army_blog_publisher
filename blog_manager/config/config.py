"""
Blog Publisher configuration
============================
Environment-driven settings for the standalone blog publishing service.
"""

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional during minimal deployments
    load_dotenv = None

from blog_manager.constants import (
    DEFAULT_FEED_KEY,
    DEFAULT_IDEAS_PREFIX,
    DEFAULT_LOCAL_WORK_ROOT,
    DEFAULT_POSTS_PREFIX,
)

if load_dotenv:
    load_dotenv()


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _list_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


SERVER_CONFIG = {
    "HOST": os.getenv("BLOG_PUBLISHER_HOST", "0.0.0.0"),
    "PORT": _int_env("BLOG_PUBLISHER_PORT", 7874),
    "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO"),
    "LOG_DIR": os.getenv("LOG_DIR", "/app/logs"),
}

AWS_CONFIG = {
    "REGION": os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1")),
    "ACCESS_KEY_ID": os.getenv("AWS_ACCESS_KEY_ID"),
    "SECRET_ACCESS_KEY": os.getenv("AWS_SECRET_ACCESS_KEY"),
    "SESSION_TOKEN": os.getenv("AWS_SESSION_TOKEN"),
}

BLOG_STORAGE_CONFIG = {
    "S3_BUCKET": os.getenv("BLOG_S3_BUCKET", ""),
    "IDEAS_PREFIX": os.getenv("BLOG_IDEAS_PREFIX", DEFAULT_IDEAS_PREFIX),
    "FEED_KEY": os.getenv("BLOG_POSTS_FEED_KEY", DEFAULT_FEED_KEY),
    "POSTS_PREFIX": os.getenv("BLOG_POSTS_PREFIX", DEFAULT_POSTS_PREFIX),
    "LOCAL_WORK_ROOT": os.getenv("BLOG_LOCAL_WORK_ROOT", DEFAULT_LOCAL_WORK_ROOT),
    "DRY_RUN": _bool_env("BLOG_DRY_RUN", False),
    "OVERWRITE_EXISTING": _bool_env("BLOG_OVERWRITE_EXISTING", False),
    "MAX_IDEAS_PER_RUN": _int_env("BLOG_MAX_IDEAS_PER_RUN", 5),
}

LLM_CONFIG = {
    "TOGETHER_MODEL": os.getenv("BLOG_TOGETHER_MODEL", ""),
    "HF_MODEL": os.getenv("BLOG_HF_MODEL", ""),
    "HF_PROVIDER": os.getenv("BLOG_HF_PROVIDER", "auto"),
    "HF_FALLBACK_MODEL_IDS": _list_env("BLOG_HF_FALLBACK_MODEL_IDS"),
    "MAX_TOKENS": _int_env("BLOG_LLM_MAX_TOKENS", 4096),
    "TEMPERATURE": _float_env("BLOG_LLM_TEMPERATURE", 0.7),
    "TOP_P": _float_env("BLOG_LLM_TOP_P", 0.9),
    "TIMEOUT_SEC": _int_env("BLOG_LLM_TIMEOUT_SEC", 90),
}

IMAGE_CONFIG = {
    "PROVIDER": os.getenv("BLOG_IMAGE_PROVIDER", ""),
    "API_KEY": os.getenv("BLOG_IMAGE_API_KEY", ""),
    "MODEL": os.getenv("BLOG_IMAGE_MODEL", ""),
    "WIDTH": _int_env("BLOG_IMAGE_WIDTH", 1200),
    "HEIGHT": _int_env("BLOG_IMAGE_HEIGHT", 630),
    "TIMEOUT_SEC": _int_env("BLOG_IMAGE_TIMEOUT_SEC", 120),
}

WORKER_CONFIG = {
    "MAX_IDEAS_PER_RUN": BLOG_STORAGE_CONFIG["MAX_IDEAS_PER_RUN"],
    "DRY_RUN": BLOG_STORAGE_CONFIG["DRY_RUN"],
}


def get_aws_client_kwargs() -> dict:
    """Return boto3 client kwargs from standard AWS environment variables."""
    kwargs = {"region_name": AWS_CONFIG["REGION"]}
    access_key_id = AWS_CONFIG.get("ACCESS_KEY_ID")
    secret_access_key = AWS_CONFIG.get("SECRET_ACCESS_KEY")

    if access_key_id and secret_access_key:
        kwargs["aws_access_key_id"] = access_key_id
        kwargs["aws_secret_access_key"] = secret_access_key
        session_token = AWS_CONFIG.get("SESSION_TOKEN")
        if session_token:
            kwargs["aws_session_token"] = session_token

    return kwargs


def get_hf_token() -> str:
    return os.getenv("BLOG_HUGGINGFACE_API_KEY", os.getenv("HUGGINGFACE_API_KEY", ""))


def get_together_token() -> str:
    return os.getenv("BLOG_TOGETHER_API_KEY", os.getenv("TOGETHER_API_KEY", ""))


def get_image_api_key() -> str:
    return IMAGE_CONFIG["API_KEY"]
