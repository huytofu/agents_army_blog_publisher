"""Initial deterministic moderation rules for blog comments."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Any


@dataclass(frozen=True)
class ModerationDecision:
    status: str
    reason: str


def determine_initial_comment_status(user: object, comment_text: str) -> ModerationDecision:
    """Return the initial moderation status for a new blog comment.

    v1 moderation is deliberately simple pseudocode made executable:
    - reject empty comments, overlong comments, obvious spam links, and banned terms
    - hold all comments from unverified users
    - hold first comments from newly verified users
    - auto-approve only users with prior approved comments and no recent rejections
    - store the reason so a future LLM moderation step can compare or override it
    """
    text = comment_text.strip()
    if not text:
        return ModerationDecision(status="rejected", reason="empty_comment")
    if len(text) > 2000:
        return ModerationDecision(status="rejected", reason="comment_too_long")
    if _looks_like_spam(text):
        return ModerationDecision(status="rejected", reason="spam_pattern")
    if not bool(_get_user_value(user, "email_verified")):
        return ModerationDecision(status="pending", reason="email_verification_required")
    if int(_get_user_value(user, "recent_rejection_count") or 0) > 0:
        return ModerationDecision(status="pending", reason="recent_rejection_requires_review")
    if int(_get_user_value(user, "approved_comment_count") or 0) > 0:
        return ModerationDecision(status="approved", reason="trusted_commenter")
    return ModerationDecision(status="pending", reason="first_comment_requires_review")


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
