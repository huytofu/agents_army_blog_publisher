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
- Minor presentation or aesthetics related edits are encouraged. You may have the freedom to:
1. Add illustration tables.
2. Highlight key phrases or words in different stylings (colors/sizes/fonts/bold/italic).
3. Extract key terms (not title) into standalone subheaders.
4. Break down long paragraphs into smaller bullet points/numbered lists.
5. Add callouts/visual cues/section dividers/footnotes to improve readability.

BOUNDARIES:
- Do not access S3. 
- Do not invoke any write tools.
- Do not invent new product claims, medical claims, or unrelated sections.
- Do not include literal raw newlines or other control characters inside JSON strings.

OUTPUT:
Do not add any text before or after the JSON.
Return ONLY valid JSON with:
{
  "body_markdown": "presentation-polished markdown - must be a single valid JSON string",
  "presentation_notes": ["short note"]
}

JSON SAFETY:
- Return one JSON object only. Do not wrap it in Markdown fences.
- Escape all line breaks as `\\n`, tabs as `\\t`, quotes as `\\"`, and backslashes as `\\\\`.
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
            "excerpt": post.excerpt,
            "body_markdown": post.body_markdown,
            "supporting_images": [
                {"filename": image.filename} for image in post.supporting_images
            ],
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = _clean_json_response(raw)
    try:
        parsed = _json_loads_lenient(text)
    except json.JSONDecodeError as exc:
        parsed = _parse_embedded_json_object(text)
        if parsed is None:
            raise exc

    if not isinstance(parsed, dict):
        raise ValueError("HTML subagent output must be a JSON object.")
    return parsed


def _clean_json_response(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _json_loads_lenient(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as strict_error:
        try:
            return json.loads(text, strict=False)
        except json.JSONDecodeError:
            raise strict_error


def _parse_embedded_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder(strict=False)
    for match in re.finditer(r"\{", text):
        try:
            parsed, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
