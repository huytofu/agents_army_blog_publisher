"""Blog content expansion agent prompt and parsing."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from blog_manager.config import EXPANSION_LLM_CONFIG
from blog_manager.schemas import (
    AgentInvocation,
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
- Do not manage subagents. If `subagent_plan` is requested by the schema, keep it to concise content handoff briefs only.

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
  "safety_notes": ["string"],
  "subagent_plan": [
    {"name": "html_subagent", "purpose": "string", "instructions": "string"},
    {"name": "image_subagent", "purpose": "string", "instructions": "string"}
  ]
}
"""


class BlogExpansionError(RuntimeError):
    """Raised when the main expansion agent cannot produce valid output."""


class BlogExpansionAgent:
    """Content agent that expands ideas and returns artifact briefs."""

    def __init__(self, llm_client: BlogLlmClient | None = None):
        self.llm_client = llm_client or BlogLlmClient(config=EXPANSION_LLM_CONFIG)

    async def expand_idea(
        self,
        idea: BlogIdea,
        *,
        revision_instruction: str = "",
    ) -> BlogAgentResult:
        """Expand one parsed idea into a structured post plus subagent plan."""
        messages = self._build_messages(idea, revision_instruction=revision_instruction)
        raw_response = await self.llm_client.chat_completion(messages)

        try:
            parsed = _parse_llm_json(raw_response)
            post = _expanded_post_from_payload(parsed)
            subagent_plan = _subagent_plan_from_payload(parsed)
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
            subagent_plan = _subagent_plan_from_payload(parsed)
            raw_response = repaired_response

        return BlogAgentResult(
            post=post,
            subagent_plan=subagent_plan,
            raw_response=raw_response,
        )

    def _build_messages(
        self,
        idea: BlogIdea,
        *,
        revision_instruction: str = "",
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _build_user_prompt(
                    idea,
                    revision_instruction=revision_instruction,
                ),
            },
        ]


def _build_user_prompt(idea: BlogIdea, *, revision_instruction: str = "") -> str:
    frontmatter = json.dumps(idea.frontmatter, indent=2, ensure_ascii=False)
    revision_section = ""
    if revision_instruction.strip():
        revision_section = f"""
## Revision instruction
{revision_instruction.strip()}
"""
    return f"""## Idea source
S3 key: {idea.key}

## Frontmatter
{frontmatter}

## Rough content
{idea.body.strip()}
{revision_section}
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


def _subagent_plan_from_payload(payload: dict[str, Any]) -> list[AgentInvocation]:
    raw_plan = payload.get("subagent_plan")
    if not isinstance(raw_plan, list):
        return _default_subagent_plan()

    plan: list[AgentInvocation] = []
    for item in raw_plan:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        purpose = str(item.get("purpose") or "").strip()
        instructions = str(item.get("instructions") or "").strip()
        if not name or not purpose or not instructions:
            continue
        plan.append(
            AgentInvocation(
                name=name,
                purpose=purpose,
                instructions=instructions,
            )
        )

    return plan or _default_subagent_plan()


def _default_subagent_plan() -> list[AgentInvocation]:
    return [
        AgentInvocation(
            name="html_subagent",
            purpose="Polish presentation and convert the expanded post into a local static HTML artifact.",
            instructions=(
                "Review the expanded post for web readability, organize the Markdown for clean "
                "section flow when useful, choose a calm Entourage presentation treatment, and "
                "create index.html under the post slug directory."
            ),
        ),
        AgentInvocation(
            name="image_subagent",
            purpose="Enhance the visual brief and create a local JPEG cover image.",
            instructions=(
                "Turn the high-level image_prompt into a production-quality cover prompt with "
                "composition, mood, palette, and safety constraints, then create cover.jpg under "
                "the post slug directory."
            ),
        ),
    ]


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise BlogExpansionError(f"Expansion output missing required field: {key}")
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
