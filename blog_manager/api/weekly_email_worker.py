"""Weekly highlighted-post email worker for confirmed blog subscribers."""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from blog_manager.api.repositories import BlogRepository
from blog_manager.api.mongo_security import sanitize_text

class EmailMessage(TypedDict):
    to_email: str
    subject: str
    body: str
    body_html: NotRequired[str]

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
    category = highlight.category.strip() or "Unknown"
    slug = highlight.slug
    excerpt = highlight.excerpt.strip()
    url = highlight.url.strip()

    body_text = _build_plain_text(
        title=title,
        category=category,
        slug=slug,
        excerpt=excerpt,
        url=url,
    )
    body_html = _build_html(
        title=title,
        category=category,
        slug=slug,
        excerpt=excerpt,
        url=url,
    )
    return {
        "to_email": to_email,
        "subject": f"Weekly highlight: {title}",
        "body": body_text,
        "body_html": body_html,
    }


def _build_plain_text(
    *,
    title: str,
    category: str,
    slug: str,
    excerpt: str,
    url: str,
) -> str:
    lines = [
        "Your weekly ENTOURAGE blog highlight",
        "",
        title,
        "",
        f"Category: {category}",
        f"Slug: {slug}",
    ]
    if excerpt:
        lines.extend(["", excerpt])
    if url:
        lines.extend(["", f"Read it: {url}"])
    return "\n".join(lines)


def _build_html(
    *,
    title: str,
    category: str,
    slug: str,
    excerpt: str,
    url: str,
) -> str:
    safe_title = html.escape(title)
    safe_category = html.escape(category)
    safe_slug = html.escape(slug)
    safe_excerpt = html.escape(excerpt)
    safe_url = html.escape(url, quote=True)

    excerpt_block = (
        f'<p style="margin:0 0 24px;font-size:16px;line-height:1.65;color:#374151;">{safe_excerpt}</p>'
        if excerpt
        else ""
    )
    cta_block = (
        f'<a href="{safe_url}" style="display:inline-block;background:#6366f1;color:#ffffff;'
        f'font-size:15px;font-weight:700;text-decoration:none;padding:12px 20px;border-radius:8px;">'
        f"Read this week's highlight</a>"
        if url
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#111827;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f5f5f5;padding:24px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;background:#ffffff;border-radius:16px;box-shadow:0 4px 12px rgba(17,24,39,0.08);">
          <tr>
            <td style="padding:32px 28px;">
              <p style="margin:0 0 10px;font-size:12px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#6366f1;">Weekly highlight</p>
              <h1 style="margin:0 0 18px;font-size:30px;line-height:1.25;font-weight:800;color:#111827;">{safe_title}</h1>
              <p style="margin:0 0 14px;">
                <span style="display:inline-block;background:#eef2ff;color:#4f46e5;font-size:12px;font-weight:800;letter-spacing:0.04em;padding:6px 12px;border-radius:999px;">{safe_category}</span>
              </p>
              <p style="margin:0 0 18px;font-size:13px;line-height:1.5;color:#6b7280;font-family:ui-monospace,Menlo,Consolas,monospace;">slug: {safe_slug}</p>
              {excerpt_block}
              {cta_block}
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _default_email_sender(message: EmailMessage) -> object:
    from blog_manager.api.ses_client import send_blog_email

    return send_blog_email(message)
