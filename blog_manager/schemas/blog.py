"""Data contracts for blog idea files, feed entries, and local artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BlogIdea:
    """Parsed S3 idea Markdown document."""

    key: str
    frontmatter: dict[str, Any]
    body: str
    raw_text: str

    @property
    def processed(self) -> bool:
        value = self.frontmatter.get("processed", False)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)


@dataclass(frozen=True)
class FeedEntry:
    """Metadata entry consumed by `website/blogs.html`."""

    slug: str
    title: str
    date: str
    excerpt: str
    coverImage: str
    contentPath: str

    def to_dict(self) -> dict[str, str]:
        return {
            "slug": self.slug,
            "title": self.title,
            "date": self.date,
            "excerpt": self.excerpt,
            "coverImage": self.coverImage,
            "contentPath": self.contentPath,
        }


@dataclass(frozen=True)
class LocalArtifact:
    """Local file prepared by a subagent for main-flow S3 publishing.

    `relative_key` is the destination S3 object key relative to the website
    bucket root, such as `blog/my-post/index.html` or `blog/my-post/cover.jpg`.
    It is not a separate parent blog post ID, but the post slug can be inferred
    from this path because generated artifacts live under `blog/<slug>/...`.
    """

    local_path: str
    relative_key: str
    content_type: str
    metadata: dict[str, str] = field(default_factory=dict)
