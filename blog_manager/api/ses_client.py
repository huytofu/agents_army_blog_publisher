"""AWS SES outbound email client for blog subscriber emails."""

from __future__ import annotations

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from blog_manager.api.weekly_email_worker import EmailMessage
from blog_manager.config import AWS_CONFIG, BLOG_API_CONFIG, get_aws_client_kwargs

logger = logging.getLogger(__name__)


def generate_rfc5322_message_id() -> str:
    from_email = _sender_email()
    domain = from_email.rsplit("@", 1)[-1] if "@" in from_email else None
    return make_msgid(domain=domain)


def send_blog_email(message: EmailMessage) -> str:
    """Send a blog email via SESv2. Returns SES MessageId."""
    to_email = message["to_email"]
    subject = message["subject"]
    body_text = message["body"]
    body_html = message.get("body_html")

    client = boto3.client("sesv2", **get_aws_client_kwargs())
    mime_bytes = _build_mime(
        to_email=to_email,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        message_id=generate_rfc5322_message_id(),
    )
    kwargs = {
        "FromEmailAddress": _sender_email(),
        "Destination": {"ToAddresses": [to_email]},
        "Content": {"Raw": {"Data": mime_bytes}},
    }
    config_set = (BLOG_API_CONFIG.get("SES_CONFIGURATION_SET") or "").strip()
    if config_set:
        kwargs["ConfigurationSetName"] = config_set

    try:
        response = client.send_email(**kwargs)
    except (BotoCoreError, ClientError) as exc:
        logger.exception("SES blog email failed to=%s", to_email)
        raise RuntimeError(f"SES send failed: {exc}") from exc

    message_id = response.get("MessageId") or ""
    logger.info("SES blog email sent to=%s message_id=%s", to_email, message_id)
    return message_id


def _build_mime(
    *,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str | None,
    message_id: str | None,
) -> bytes:
    msg = MIMEMultipart("alternative")
    from_email = _sender_email()
    msg["From"] = formataddr(("Entourage Blog", from_email))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Reply-To"] = from_email
    if message_id:
        msg["Message-ID"] = message_id

    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    html_body = body_html or f"<html><body><p>{body_text.replace(chr(10), '<br/>')}</p></body></html>"
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg.as_bytes()


def _sender_email() -> str:
    from_email = (BLOG_API_CONFIG.get("SES_SENDER_EMAIL") or "").strip()
    if not from_email:
        raise RuntimeError("BLOG_API_SES_SENDER_EMAIL is required for blog email sends.")
    return from_email
