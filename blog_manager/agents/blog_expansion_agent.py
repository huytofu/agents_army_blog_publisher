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
    SupportingImage,
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
- Add 1 to 2 supporting image placeholders as full-line JPEG markers like `{image_001.jpg}` in `body_markdown`.
- For every supporting image placeholder, add one matching `supporting_images` item with filename, prompt, and alt_text.
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
  "supporting_images": [
    {
      "filename": "image_001.jpg",
      "prompt": "specific supporting image generation prompt",
      "alt_text": "accessible image description"
    }
  ],
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
        "supporting_images": [
            {
                "filename": image.filename,
                "prompt": image.prompt,
                "alt_text": image.alt_text,
            }
            for image in post.supporting_images
        ],
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
    supporting_images = _supporting_images_from_payload(payload.get("supporting_images"), body_markdown)

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
        supporting_images=supporting_images,
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


def _supporting_images_from_payload(value: Any, body_markdown: str) -> list[SupportingImage]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise BlogExpansionError("supporting_images must be a list.")

    images: list[SupportingImage] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise BlogExpansionError("supporting_images items must be objects.")
        filename = str(item.get("filename") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        alt_text = str(item.get("alt_text") or "").strip()
        if not re.fullmatch(r"image_\d{3}\.jpg", filename):
            raise BlogExpansionError(
                "supporting_images filenames must look like image_001.jpg."
            )
        if filename in seen:
            raise BlogExpansionError(f"Duplicate supporting image filename: {filename}")
        if not prompt:
            raise BlogExpansionError(f"Supporting image {filename} is missing prompt.")
        if not alt_text:
            raise BlogExpansionError(f"Supporting image {filename} is missing alt_text.")
        placeholder = "{" + filename + "}"
        count = len(re.findall(rf"(?m)^\s*{re.escape(placeholder)}\s*$", body_markdown))
        if count != 1:
            raise BlogExpansionError(
                f"Supporting image {filename} must appear exactly once as a full-line placeholder."
            )
        seen.add(filename)
        images.append(SupportingImage(filename=filename, prompt=prompt, alt_text=alt_text))
    return images
