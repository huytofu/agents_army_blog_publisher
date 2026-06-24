"""Blog content expansion agent prompt and parsing."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from blog_manager.config import EXPANSION_LLM_CONFIG
from blog_manager.schemas import (
    BlogAgentResult,
    BlogIdea,
    ExpandedPost,
)
from blog_manager.services.idea_parser import slugify
from blog_manager.services.llm_client import BlogLlmClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are BlogExpansionAgent, the Entourage blog content specialist.

ROLE:
- Expand rough Markdown blog ideas into complete, engaging Entourage blog posts.
- Preserve the user's intent and do not invent product capabilities.
- Use a warm, practical, emotionally grounded tone.
- Avoid medical diagnosis, guaranteed outcomes, or clinical treatment claims.

CONTENT RESPONSIBILITIES:
- Write publication-ready Markdown with a strong title, useful headings, short paragraphs, and a grounded closing reflection.
- Provide a concise excerpt and SEO metadata that accurately summarize the post.
- Provide a high-level `image_prompt` describing the desired cover mood and subject.
- Include `safety_notes` for any claims or wording that should remain cautious.
- Limit the post length to minimum of 700 words and maximum of 900 words.
- Use plenty of emoticons at both mid and end of sentences 
- Use occasional humor throughout the post (no dark humor, threat, or triggering content allowed)

BOUNDARIES:
- Do not decide workflow routing, publishing, retries, or failure handling.
- Do not perform S3 operations.
- Do not render HTML or generate images.
- Do not manage subagents or produce subagent handoff plans.

OUTPUT:
Return ONLY valid JSON with exactly these top-level fields:
{
  "title": "string",
  "slug": "kebab-case-string",
  "date": "YYYY-MM-DD",
  "excerpt": "string",
  "body_markdown": "string",
  "image_prompt": "string",
  "seo_title": "string",
  "seo_description": "string",
  "safety_notes": ["string"]
}
"""


class BlogExpansionError(RuntimeError):
    """Raised when the main expansion agent cannot produce valid output."""


class BlogExpansionAgent:
    """Content agent that expands ideas and revises expanded posts."""

    def __init__(self, llm_client: BlogLlmClient | None = None):
        self.llm_client = llm_client or BlogLlmClient(config=EXPANSION_LLM_CONFIG)

    async def expand_idea(
        self,
        idea: BlogIdea,
    ) -> BlogAgentResult:
        """Expand one parsed idea into a structured post."""
        messages = self._build_messages(_build_expansion_user_prompt(idea))
        raw_response = await self.llm_client.chat_completion(messages)

        try:
            parsed = _parse_llm_json(raw_response)
            post = _expanded_post_from_payload(parsed)
        except BlogExpansionError:
            logger.warning("Main expansion output invalid; retrying with repair prompt")
            repaired_response = await self.llm_client.chat_completion(
                [
                    *messages,
                    {"role": "assistant", "content": raw_response},
                    {
                        "role": "user",
                        "content": "Repair the previous output. Return only valid JSON matching the required schema.",
                    },
                ]
            )
            parsed = _parse_llm_json(repaired_response)
            post = _expanded_post_from_payload(parsed)
            raw_response = repaired_response

        return BlogAgentResult(
            post=post,
            raw_response=raw_response,
        )

    async def revise_content(
        self,
        post: ExpandedPost,
        *,
        revision_instruction: str,
    ) -> BlogAgentResult:
        """Revise the current expanded post using supervisor instructions."""
        messages = self._build_messages(
            _build_revision_user_prompt(
                post,
                revision_instruction=revision_instruction,
            )
        )
        raw_response = await self.llm_client.chat_completion(messages)

        try:
            parsed = _parse_llm_json(raw_response)
            revised_post = _expanded_post_from_payload(parsed)
        except BlogExpansionError:
            logger.warning("Content revision output invalid; retrying with repair prompt")
            repaired_response = await self.llm_client.chat_completion(
                [
                    *messages,
                    {"role": "assistant", "content": raw_response},
                    {
                        "role": "user",
                        "content": "Repair the previous output. Return only valid JSON matching the required schema.",
                    },
                ]
            )
            parsed = _parse_llm_json(repaired_response)
            revised_post = _expanded_post_from_payload(parsed)
            raw_response = repaired_response

        return BlogAgentResult(post=revised_post, raw_response=raw_response)

    def _build_messages(
        self,
        user_prompt: str,
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]


def _build_expansion_user_prompt(idea: BlogIdea) -> str:
    frontmatter = json.dumps(idea.frontmatter, indent=2, ensure_ascii=False)
    return f"""## Idea source
S3 key: {idea.key}

## Frontmatter
{frontmatter}

## Rough content
{idea.body.strip()}
"""


def _build_revision_user_prompt(
    post: ExpandedPost,
    *,
    revision_instruction: str,
) -> str:
    post_payload = {
        "title": post.title,
        "slug": post.slug,
        "date": post.date,
        "excerpt": post.excerpt,
        "body_markdown": post.body_markdown,
        "image_prompt": post.image_prompt,
        "seo_title": post.seo_title,
        "seo_description": post.seo_description,
        "safety_notes": post.safety_notes,
    }
    return f"""## Current expanded post
{json.dumps(post_payload, indent=2, ensure_ascii=False)}

## Revision instruction
{revision_instruction.strip()}
"""


def _parse_llm_json(raw: str) -> dict[str, Any]:
    text = _clean_response(raw)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise BlogExpansionError("Expansion output did not contain JSON.")
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError as exc:
            raise BlogExpansionError(f"Expansion JSON parse failed: {exc}") from exc

    if not isinstance(parsed, dict):
        raise BlogExpansionError("Expansion output must be a JSON object.")
    return parsed


def _clean_response(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _expanded_post_from_payload(payload: dict[str, Any]) -> ExpandedPost:
    title = _required_string(payload, "title")
    slug = slugify(str(payload.get("slug") or title))
    post_date = str(payload.get("date") or date.today().isoformat()).strip()
    excerpt = _required_string(payload, "excerpt")
    body_markdown = _required_string(payload, "body_markdown")
    image_prompt = _required_string(payload, "image_prompt")

    return ExpandedPost(
        title=title,
        slug=slug,
        date=post_date,
        excerpt=excerpt,
        body_markdown=body_markdown,
        image_prompt=image_prompt,
        seo_title=str(payload.get("seo_title") or title).strip(),
        seo_description=str(payload.get("seo_description") or excerpt).strip(),
        safety_notes=_string_list(payload.get("safety_notes")),
    )


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise BlogExpansionError(f"Expansion output missing required field: {key}")
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
