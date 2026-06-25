"""HTML subagent for creating local static blog article artifacts."""

from __future__ import annotations

from dataclasses import replace
import json
import logging
import re
from typing import Any

from blog_manager.config import SUBAGENT_LLM_CONFIG
from blog_manager.schemas import ExpandedPost, LocalArtifact
from blog_manager.services.llm_client import BlogLlmClient
from blog_manager.tools.html_write_tool import HtmlWriteTool

logger = logging.getLogger(__name__)

HTML_SUBAGENT_PROMPT = """You are html_subagent. An expert in HTML and web content presentation/accessibility.

MISSION:
Create a local static HTML article artifact that reads well on the Entourage blog.

VALUE-ADDED RESPONSIBILITIES:
- Improve web readability & accessibility without changing the core meaning of the post.
- Normalize paragraph spacing, heading flow, and list formatting before rendering.
- Preserve the majority of sentences and words. Do not add new sections or paragraphs.
- Preserve supporting image placeholder lines exactly as-is, such as `{image_001.jpg}`.
  These full-line markers are replaced with image tags by the local renderer after your pass.
- Minor presentation or aesthetics related edits are encouraged. You may have the freedom to:
1. Add illustration tables or diagrams like Mermaid diagrams
2. Highlight key phrases or words in different stylings (colors/sizes/fonts/bold/italic)
3. Extract key terms into standalone subheadings/subheaders
4. Break down long paragraphs into smaller bullet points/numbered lists
5. Add callouts/visual cues or breaks/section dividers/footnotes to improve readability or aesthetics

BOUNDARIES:
- Do not access S3.
- Do not invent new product claims, medical claims, or unrelated sections.
- Return only JSON for the preparation step. The graph will invoke the local write tool after your preparation.

OUTPUT:
Return ONLY valid JSON with:
{
  "body_markdown": "presentation-polished markdown",
  "presentation_notes": ["short note"]
}
"""


class HtmlAgent:
    """Subagent facade for converting expanded posts into local HTML files."""

    def __init__(
        self,
        html_tool: HtmlWriteTool | None = None,
        llm_client: BlogLlmClient | None = None,
    ):
        self.html_tool = html_tool or HtmlWriteTool()
        self.llm_client = llm_client or BlogLlmClient(config=SUBAGENT_LLM_CONFIG)

    async def create_html_artifact(
        self,
        post: ExpandedPost,
        *,
        instructions: str = "",
        prior_errors: list[str] | None = None,
    ) -> LocalArtifact:
        """Create `index.html` locally after a presentation polish pass."""
        polished_post, notes = await self._prepare_post_for_html(
            post,
            instructions=instructions,
            prior_errors=prior_errors or [],
        )
        artifact = self.html_tool.write_article_html(polished_post)
        artifact.metadata.update(
            {
                "presentation_agent": "html_subagent",
                "presentation_pass": "readability_and_layout",
                "presentation_notes": " | ".join(notes),
            }
        )
        return artifact

    async def _prepare_post_for_html(
        self,
        post: ExpandedPost,
        *,
        instructions: str,
        prior_errors: list[str],
    ) -> tuple[ExpandedPost, list[str]]:
        try:
            raw = await self.llm_client.chat_completion(
                [
                    {"role": "system", "content": HTML_SUBAGENT_PROMPT},
                    {
                        "role": "user",
                        "content": _build_html_user_prompt(
                            post,
                            instructions=instructions,
                            prior_errors=prior_errors,
                        ),
                    },
                ]
            )
            payload = _parse_json_object(raw)
            body_markdown = str(payload.get("body_markdown") or "").strip()
            if body_markdown:
                return replace(post, body_markdown=body_markdown), _string_list(
                    payload.get("presentation_notes")
                )
        except Exception as exc:
            logger.warning("HTML subagent brain failed; using deterministic fallback: %s", exc)

        fallback_post = _prepare_post_for_html(post, instructions=instructions)
        return fallback_post, ["deterministic readability fallback used"]


def _prepare_post_for_html(post: ExpandedPost, *, instructions: str = "") -> ExpandedPost:
    body_markdown = _normalize_markdown_flow(post.body_markdown)
    return replace(post, body_markdown=body_markdown)


def _normalize_markdown_flow(markdown: str) -> str:
    """Apply conservative Markdown formatting for better static HTML rendering."""
    lines = [line.rstrip() for line in markdown.strip().splitlines()]
    normalized: list[str] = []
    previous_blank = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if not previous_blank:
                normalized.append("")
            previous_blank = True
            continue

        if re.fullmatch(r"\d+[.)]\s+.+", stripped):
            stripped = "- " + re.sub(r"^\d+[.)]\s+", "", stripped)

        if normalized and stripped.startswith(("# ", "## ", "### ")) and normalized[-1] != "":
            normalized.append("")

        normalized.append(stripped)
        previous_blank = False

    return "\n".join(normalized).strip()


def _build_html_user_prompt(
    post: ExpandedPost,
    *,
    instructions: str,
    prior_errors: list[str],
) -> str:
    payload = {
        "instructions": instructions,
        "prior_tool_or_validation_errors": prior_errors,
        "post": {
            "title": post.title,
            "slug": post.slug,
            "date": post.date,
            "excerpt": post.excerpt,
            "body_markdown": post.body_markdown,
            "seo_title": post.seo_title,
            "seo_description": post.seo_description,
            "primary_keyword": post.primary_keyword,
            "search_intent": post.search_intent,
            "category": post.category,
            "faq_items": post.faq_items,
            "citation_suggestions": post.citation_suggestions,
            "safety_notes": post.safety_notes,
            "tags": post.tags,
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


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
        raise ValueError("HTML subagent output must be a JSON object.")
    return parsed


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
