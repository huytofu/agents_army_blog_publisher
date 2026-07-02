"""Subscriber lifecycle emails sent through the blog API."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import HTTPException, Request, status

from blog_manager.api.config import BlogApiSettings
from blog_manager.api.ses_client import send_blog_email


def resolve_api_base_url(*, settings: BlogApiSettings, request: Request) -> str:
    """Return the public API base URL used in outbound subscriber links."""
    configured = settings.api_base_url.strip().rstrip("/")
    if configured:
        return configured

    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    if forwarded_proto and forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}".rstrip("/")

    netloc = request.url.netloc
    if netloc:
        scheme = request.url.scheme or "https"
        return f"{scheme}://{netloc}".rstrip("/")

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="BLOG_API_BASE_URL is required for subscription confirmation emails.",
    )


def build_subscription_confirm_url(*, settings: BlogApiSettings, request: Request, token: str) -> str:
    base_url = resolve_api_base_url(settings=settings, request=request)
    return f"{base_url}/blog/subscribers/confirm?token={quote(token, safe='')}"


def send_subscription_confirmation_email(*, to_email: str, confirm_url: str) -> str:
    """Send the double opt-in confirmation email. Returns the SES MessageId."""
    subject = "Confirm your ENTOURAGE blog subscription"
    body_text = (
        "Thanks for subscribing to the ENTOURAGE weekly blog highlight.\n\n"
        f"Confirm your subscription:\n{confirm_url}\n"
    )
    body_html = (
        "<html><body>"
        "<p>Thanks for subscribing to the ENTOURAGE weekly blog highlight.</p>"
        f'<p><a href="{confirm_url}">Confirm your subscription</a></p>'
        "<p>If you did not request this, you can ignore this email.</p>"
        "</body></html>"
    )
    return send_blog_email(
        {
            "to_email": to_email,
            "subject": subject,
            "body": body_text,
            "body_html": body_html,
        }
    )
