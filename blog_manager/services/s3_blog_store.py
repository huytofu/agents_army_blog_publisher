"""S3 storage adapter for the main blog publishing flow.

Only main-flow publisher code should receive this store. Subagents should receive
local-only tools and pass local artifact descriptors back to the main flow.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from blog_manager.config import BLOG_STORAGE_CONFIG, get_aws_client_kwargs
from blog_manager.constants import (
    IDEA_MARKDOWN_CONTENT_TYPE,
    POSTS_JSON_CONTENT_TYPE,
)
from blog_manager.schemas import BlogIdea, FeedEntry, LocalArtifact
from blog_manager.services.idea_parser import (
    is_idea_key,
    mark_idea_processed,
    parse_idea_markdown,
)


class BlogStoreError(RuntimeError):
    """Raised when S3 blog storage cannot complete an operation."""


class S3BlogStore:
    """Read and publish blog assets in the configured S3 bucket."""

    def __init__(self, client: Any | None = None, config: dict[str, Any] | None = None):
        self.config = config or BLOG_STORAGE_CONFIG
        self.bucket = self.config.get("S3_BUCKET", "")
        self.ideas_prefix = self.config["IDEAS_PREFIX"]
        self.feed_key = self.config["FEED_KEY"]
        self.posts_prefix = self.config["POSTS_PREFIX"]
        self.client = client or self._create_s3_client()

    def list_idea_keys(self, *, max_items: int | None = None) -> list[str]:
        """List S3 idea keys matching `idea_<integer>.md`."""
        self._require_bucket()
        keys: list[str] = []

        paginator = self.client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=self.bucket, Prefix=self.ideas_prefix)
        for page in pages:
            for item in page.get("Contents", []):
                key = item.get("Key", "")
                if not is_idea_key(key):
                    continue
                keys.append(key)
                if max_items and len(keys) >= max_items:
                    return sorted(keys)

        return sorted(keys)

    def read_text(self, key: str) -> str:
        """Read a UTF-8 S3 object as text."""
        self._require_bucket()
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        body = response["Body"].read()
        return body.decode("utf-8")

    def read_idea(self, key: str) -> BlogIdea:
        """Read and parse an idea Markdown object."""
        return parse_idea_markdown(key, self.read_text(key))

    def list_unprocessed_ideas(self, *, max_items: int | None = None) -> list[BlogIdea]:
        """Return unprocessed idea documents in key order."""
        ideas: list[BlogIdea] = []
        for key in self.list_idea_keys():
            idea = self.read_idea(key)
            if idea.processed:
                continue
            ideas.append(idea)
            if max_items and len(ideas) >= max_items:
                break
        return ideas

    def read_posts_feed(self) -> list[dict[str, Any]]:
        """Read `blog/posts.json`; return an empty feed if the object is absent."""
        try:
            raw_feed = self.read_text(self.feed_key)
        except Exception as exc:
            if _is_s3_not_found(exc):
                return []
            raise

        if not raw_feed.strip():
            return []

        parsed = json.loads(raw_feed)
        if not isinstance(parsed, list):
            raise BlogStoreError("Blog posts feed must be a JSON array.")
        return [dict(item) for item in parsed if isinstance(item, dict)]

    def append_feed_entry(self, entry: FeedEntry | dict[str, Any]) -> list[dict[str, Any]]:
        """Append one metadata entry to `posts.json`, deduping by slug."""
        entry_dict = entry.to_dict() if isinstance(entry, FeedEntry) else dict(entry)
        slug = entry_dict.get("slug")
        if not slug:
            raise BlogStoreError("Feed entry requires a slug.")

        feed = self.read_posts_feed()
        without_slug = [item for item in feed if item.get("slug") != slug]
        updated_feed = _sort_feed([entry_dict, *without_slug])
        self.write_posts_feed(updated_feed)
        return updated_feed

    def write_posts_feed(self, entries: Iterable[dict[str, Any]]) -> None:
        """Upload the normalized `blog/posts.json` feed."""
        normalized = _sort_feed([dict(item) for item in entries])
        payload = json.dumps(normalized, indent=2, ensure_ascii=False) + "\n"
        self.put_text(self.feed_key, payload, content_type=POSTS_JSON_CONTENT_TYPE)

    def upload_local_artifact(self, artifact: LocalArtifact | dict[str, Any]) -> None:
        """Upload a local file prepared by the main flow to its S3 key."""
        data = artifact if isinstance(artifact, dict) else artifact.__dict__
        local_path = Path(data["local_path"])
        if not local_path.is_file():
            raise BlogStoreError(f"Local artifact does not exist: {local_path}")

        relative_key = str(data["relative_key"]).lstrip("/")
        content_type = data["content_type"]
        self._require_bucket()
        with local_path.open("rb") as file_obj:
            self.client.put_object(
                Bucket=self.bucket,
                Key=relative_key,
                Body=file_obj,
                ContentType=content_type,
            )

    def mark_idea_processed(self, idea: BlogIdea, *, slug: str, post_key: str) -> None:
        """Flip the original S3 idea file to processed after publication succeeds."""
        updated_markdown = mark_idea_processed(idea.raw_text, slug=slug, post_key=post_key)
        self.overwrite_source_idea(idea.key, updated_markdown)

    def overwrite_source_idea(self, key: str, markdown: str) -> None:
        """Overwrite the original S3 idea object with updated Markdown."""
        self.put_text(
            key,
            markdown,
            content_type=IDEA_MARKDOWN_CONTENT_TYPE,
        )

    def put_text(self, key: str, text: str, *, content_type: str) -> None:
        """Upload a UTF-8 text object to S3."""
        self._require_bucket()
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=text.encode("utf-8"),
            ContentType=content_type,
        )

    def _require_bucket(self) -> None:
        if not self.bucket:
            raise BlogStoreError("BLOG_S3_BUCKET is required for S3 blog storage.")

    @staticmethod
    def _create_s3_client() -> Any:
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:
            raise BlogStoreError("boto3 is required for S3 blog storage.") from exc

        return boto3.client("s3", **get_aws_client_kwargs())


def _sort_feed(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(entries, key=lambda item: str(item.get("date") or ""), reverse=True)


def _is_s3_not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error", {})
    code = str(error.get("Code", ""))
    return code in {"NoSuchKey", "404", "NotFound"}
