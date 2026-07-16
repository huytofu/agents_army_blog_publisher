"""Blog account verification emails sent through the blog API."""

from __future__ import annotations

from urllib.parse import quote

from blog_manager.api.ses_client import send_blog_email
from blog_manager.config import BLOG_STORAGE_CONFIG


def build_verify_email_url(*, token: str) -> str:
    """Return the static-site URL that completes email verification."""
    site_url = str(BLOG_STORAGE_CONFIG.get("SITE_URL") or "https://www.entourage-ai.life").rstrip("/")
    return f"{site_url}/blog/verify-email.html?token={quote(token, safe='')}"


def send_verification_email(*, to_email: str, verify_url: str) -> str:
    """Send the blog account verification email. Returns the SES MessageId."""
    subject = "Verify your ENTOURAGE blog account"
    body_text = (
        "Thanks for creating an ENTOURAGE blog account.\n\n"
        f"Verify your email to comment on posts:\n{verify_url}\n"
    )
    body_html = (
        "<html><body>"
        "<p>Thanks for creating an ENTOURAGE blog account.</p>"
        f'<p><a href="{verify_url}">Verify your email</a> to comment on posts.</p>'
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
