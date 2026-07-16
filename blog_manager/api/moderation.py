"""Deterministic and LLM-assisted moderation rules for blog comments."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from typing import Any, Mapping

from blog_manager.api.llm_client import BlogApiLlmClient

logger = logging.getLogger(__name__)

COMMENT_MODERATION_PROMPT = """You are a blog comment content moderator for a supportive wellness community.

TASK:
Review the user comment and decide whether it should be published.

REJECT the comment if it contains ANY of these violations:
1. profanity — swear words, vulgar language, or obscene expressions
2. insults — triggering insults, personal attacks, bullying, or derogatory remarks
3. spam_links — promotional URLs, suspicious links, or link spam
4. pornographic — sexual content, explicit material, or pornographic suggestions
5. hate_speech — slurs, discriminatory language, or attacks on protected groups
6. threats_harassment — threats of violence, encouragement of self-harm, or targeted harassment

APPROVE the comment only when it is free of all violations above.
Lean more towards approving for respectful disagreement, mild frustration, or benign off-topic remarks.

OUTPUT:
Use these reason codes exactly:
- profanity
- insults
- spam_links
- pornographic
- hate_speech
- threats_harassment
- none (only when decision is approve)
Do not add any text before or after the JSON object.
Return ONLY valid JSON with no text before or after:
{
  "decision": "approve" or "reject",
  "reason": "<violation_code or none>"
}
"""


@dataclass(frozen=True)
class ModerationDecision:
    status: str
    reason: str


def determine_initial_comment_status(user: object, comment_text: str) -> ModerationDecision:
    """Return the initial moderation status for a new blog comment.

    v1 moderation is deliberately simple pseudocode made executable:
    - reject empty comments, overlong comments, and obvious spam links
    - hold all comments from unverified users
    - hold first comments from newly verified users
    - auto-approve only users with prior approved comments and no recent rejections
    - store the reason so a future LLM moderation step can compare or override it
    """
    content_decision = _content_moderation_checks(comment_text)
    if content_decision is not None:
        return content_decision
    return _user_trust_moderation(user)


async def determine_comment_status(
    user: object,
    comment_text: str,
    *,
    llm_client: BlogApiLlmClient | None = None,
) -> ModerationDecision:
    """Run content checks, optional LLM moderation, then user-trust rules."""
    content_decision = _content_moderation_checks(comment_text)
    if content_decision is not None:
        return content_decision

    llm_decision = await run_llm_comment_moderation(comment_text, llm_client=llm_client)
    if llm_decision is not None and llm_decision.status == "rejected":
        return llm_decision

    return _user_trust_moderation(user)


async def run_llm_comment_moderation(
    comment_text: str,
    *,
    llm_client: BlogApiLlmClient | None = None,
) -> ModerationDecision | None:
    """Ask the LLM to approve or reject comment content. Returns None on graceful skip."""
    client = llm_client or BlogApiLlmClient()
    try:
        raw = await client.chat_completion(
            [
                {"role": "system", "content": COMMENT_MODERATION_PROMPT},
                {"role": "user", "content": f"Comment to review:\n{comment_text.strip()}"},
            ]
        )
        return _parse_llm_moderation_response(raw)
    except Exception as exc:
        logger.warning("LLM comment moderation skipped after failure: %s", exc)
        return None


def _content_moderation_checks(comment_text: str) -> ModerationDecision | None:
    text = comment_text.strip()
    if not text:
        return ModerationDecision(status="rejected", reason="empty_comment")
    if len(text) > 2000:
        return ModerationDecision(status="rejected", reason="comment_too_long")
    if _looks_like_spam(text):
        return ModerationDecision(status="rejected", reason="spam_pattern")
    return None


def _user_trust_moderation(user: object) -> ModerationDecision:
    if not bool(_get_user_value(user, "email_verified")):
        return ModerationDecision(status="pending", reason="email_verification_required")
    if int(_get_user_value(user, "recent_rejection_count") or 0) > 0:
        return ModerationDecision(status="pending", reason="recent_rejection_requires_review")
    if int(_get_user_value(user, "approved_comment_count") or 0) > 0:
        return ModerationDecision(status="approved", reason="trusted_commenter")
    return ModerationDecision(status="pending", reason="first_comment_requires_review")


def _parse_llm_moderation_response(raw: str) -> ModerationDecision | None:
    try:
        payload = _parse_json_object(raw)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("LLM comment moderation skipped after invalid JSON: %s", exc)
        return None

    decision = str(payload.get("decision", "")).strip().casefold()
    reason = str(payload.get("reason", "")).strip().casefold() or "llm_rejected"

    if decision == "reject":
        if reason in {"", "none"}:
            reason = "llm_rejected"
        return ModerationDecision(status="rejected", reason=f"llm_{reason}")
    if decision == "approve":
        return ModerationDecision(status="approved", reason="llm_approved")
    logger.warning("LLM comment moderation skipped after unknown decision=%r", decision)
    return None


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    parsed = json.loads(text.strip())
    if not isinstance(parsed, dict):
        raise ValueError("LLM moderation output must be a JSON object.")
    return parsed


def _looks_like_spam(text: str) -> bool:
    lowered = text.casefold()
    banned_terms = ("buy now", "free crypto", "casino", "loan offer")
    if any(term in lowered for term in banned_terms):
        return True
    return len(re.findall(r"https?://", lowered)) > 0


def _get_user_value(user: object, name: str) -> Any:
    if isinstance(user, Mapping):
        return user.get(name)
    return getattr(user, name, None)
