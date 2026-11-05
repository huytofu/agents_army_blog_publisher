"""Parsing helpers for S3-backed blog idea Markdown files."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

from blog_manager.schemas import BlogIdea

_FRONTMATTER_DELIMITER = "---"
_SLUG_SEPARATOR = "-"


class IdeaParseError(ValueError):
    """Raised when an idea file cannot be parsed safely."""


def parse_idea_markdown(key: str, raw_text: str) -> BlogIdea:
    """Parse an idea Markdown file into frontmatter and rough body text."""
    frontmatter_text, body = _split_frontmatter(raw_text)
    frontmatter = _parse_frontmatter(frontmatter_text)
    return BlogIdea(
        key=key,
        frontmatter=frontmatter,
        body=body,
        raw_text=raw_text,
    )


def mark_idea_processed(raw_text: str, *, slug: str, post_key: str) -> str:
    """Return Markdown with processed metadata updated and body preserved."""
    frontmatter_text, body = _split_frontmatter(raw_text)
    frontmatter = _parse_frontmatter(frontmatter_text)
    frontmatter.update(
        {
            "processed": True,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "slug": slug,
            "post_key": post_key,
        }
    )
    return _render_frontmatter(frontmatter) + body


def related_slugs_from_frontmatter(value: Any) -> list[str]:
    """Read pinned related-post slugs from idea frontmatter.

    Accepts a YAML list or a comma-separated string. Blank items are dropped,
    duplicates are ignored, and the first-seen order is kept.
    """
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = [str(item) for item in value]
    elif isinstance(value, str):
        raw_items = value.split(",")
    else:
        text = str(value).strip()
        raw_items = [text] if text else []

    slugs: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        slug = item.strip()
        if not slug:
            continue
        key = slug.casefold()
        if key in seen:
            continue
        seen.add(key)
        slugs.append(slug)
    return slugs


def slugify(value: str, *, fallback: str = "blog-post") -> str:
    """Create a URL-safe slug from a title or hint."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_value.lower()
    slug = re.sub(r"[^a-z0-9]+", _SLUG_SEPARATOR, lowered)
    slug = slug.strip(_SLUG_SEPARATOR)
    return slug or fallback


def is_idea_key(key: str) -> bool:
    """Return true for `blog/ideas/idea_<integer>.md`-style keys."""
    filename = key.rsplit("/", 1)[-1]
    return bool(re.fullmatch(r"idea_\d+\.md", filename))


def _split_frontmatter(raw_text: str) -> tuple[str, str]:
    if not raw_text.startswith(_FRONTMATTER_DELIMITER):
        return "", raw_text

    lines = raw_text.splitlines(keepends=True)
    if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
        return "", raw_text

    for index in range(1, len(lines)):
        if lines[index].strip() == _FRONTMATTER_DELIMITER:
            frontmatter = "".join(lines[1:index])
            body = "".join(lines[index + 1 :])
            return frontmatter, body

    raise IdeaParseError("Idea frontmatter starts with --- but has no closing delimiter.")


def _parse_frontmatter(frontmatter_text: str) -> dict[str, Any]:
    if not frontmatter_text.strip():
        return {}

    try:
        import yaml

        parsed = yaml.safe_load(frontmatter_text) or {}
        if not isinstance(parsed, dict):
            raise IdeaParseError("Idea frontmatter must be a mapping.")
        return dict(parsed)
    except ImportError:
        return _parse_simple_frontmatter(frontmatter_text)


def _parse_simple_frontmatter(frontmatter_text: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for raw_line in frontmatter_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise IdeaParseError(f"Invalid frontmatter line: {raw_line}")
        key, value = line.split(":", 1)
        parsed[key.strip()] = _parse_scalar(value.strip())
    return parsed


def _parse_scalar(value: str) -> Any:
    if value == "":
        return ""
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def _render_frontmatter(frontmatter: dict[str, Any]) -> str:
    lines = [_FRONTMATTER_DELIMITER + "\n"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            lines.append(_render_list_field(key, value))
        else:
            lines.append(f"{key}: {_render_scalar(value)}\n")
    lines.append(_FRONTMATTER_DELIMITER + "\n")
    return "".join(lines)


def _render_list_field(key: str, value: list[Any]) -> str:
    if not value:
        return f"{key}: []\n"
    items = "".join(f"  - {_render_scalar(item)}\n" for item in value)
    return f"{key}:\n{items}"


def _render_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    text = str(value)
    if not text or any(token in text for token in [":", "#", "\n", '"']):
        escaped = text.replace('"', '\\"')
        return f'"{escaped}"'
    return text
