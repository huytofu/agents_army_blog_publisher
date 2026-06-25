"""Weekly highlighted-post email worker for confirmed blog subscribers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from blog_manager.api.repositories import BlogRepository
from blog_manager.api.mongo_security import sanitize_text

EmailMessage = dict[str, str]
EmailSender = Callable[[EmailMessage], object]

WEEKLY_HIGHLIGHT_EXAMPLE = {
    "slug": "healing-through-habits",
    "title": "Healing Through Habits",
    "excerpt": "A practical reflection.",
    "url": "https://www.entourage-ai.life/blog/healing-through-habits/index.html",
    "category": "Habits",
    "tags": ["habits", "reflection", "inner-work"],
    "selected_at": "2026-06-25T00:00:00Z",
}


class WeeklyHighlight(BaseModel):
    """Validated `weekly_highlight.json` contract for the email worker."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(description="Post slug from blog/posts.json.")
    title: str = Field(description="Highlighted post title.")
    excerpt: str = Field(description="Short email teaser for the highlighted post.")
    url: str = Field(description="Absolute canonical URL for the highlighted post.")
    category: str = Field(default="", description="Optional post category for segmentation/reporting.")
    tags: list[str] = Field(default_factory=list, description="Optional post tags for segmentation/reporting.")
    selected_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the highlight artifact was generated.",
    )

    @field_validator("slug", mode="before")
    @classmethod
    def normalize_slug(cls, value: str) -> str:
        return sanitize_text(str(value).strip().casefold(), max_length=120)

    @field_validator("title", "excerpt", "url", "category", mode="before")
    @classmethod
    def sanitize_short_text(cls, value: str) -> str:
        return sanitize_text(str(value).strip(), max_length=500)

    @field_validator("tags", mode="before")
    @classmethod
    def sanitize_tags(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("Weekly highlight tags must be a list.")
        return [sanitize_text(str(item).strip(), max_length=80) for item in value if str(item).strip()]


def parse_weekly_highlight(value: Mapping[str, object]) -> WeeklyHighlight:
    """Validate data loaded from `weekly_highlight.json` before email sends."""
    return WeeklyHighlight.model_validate(dict(value))


def run_weekly_highlight_email_job(
    *,
    repository: BlogRepository,
    highlight: Mapping[str, object],
    send_email: EmailSender | None = None,
) -> dict[str, int]:
    """Send the weekly highlighted post once per confirmed subscriber.

    `highlight` is expected to come from the future `blog/weekly-highlight.json`
    artifact. The repository records send events so retries do not duplicate
    emails for the same subscriber and highlight slug.
    """
    parsed_highlight = parse_weekly_highlight(highlight)
    slug = parsed_highlight.slug
    resolved_sender = send_email or _default_email_sender

    attempted = 0
    sent = 0
    skipped = 0
    for subscriber in repository.list_confirmed_subscribers():
        attempted += 1
        if repository.has_digest_send(email=subscriber.email, highlight_slug=slug):
            skipped += 1
            continue
        resolved_sender(_build_message(to_email=subscriber.email, highlight=parsed_highlight))
        repository.record_digest_send(email=subscriber.email, highlight_slug=slug)
        sent += 1
    return {"attempted": attempted, "sent": sent, "skipped": skipped}


def _build_message(*, to_email: str, highlight: WeeklyHighlight) -> EmailMessage:
    title = highlight.title or "This week's ENTOURAGE highlight"
    excerpt = highlight.excerpt
    url = highlight.url
    body_parts = [title]
    if excerpt:
        body_parts.extend(["", excerpt])
    if url:
        body_parts.extend(["", f"Read it: {url}"])
    return {
        "to_email": to_email,
        "subject": f"Weekly highlight: {title}",
        "body": "\n".join(body_parts),
    }


def _default_email_sender(message: EmailMessage) -> object:
    from blog_manager.api.ses_client import send_blog_email

    return send_blog_email(message)
