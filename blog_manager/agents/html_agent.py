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
- Improve web readability & accessibility while preserving the majority of sentences and words.
- Normalize paragraph spacing, heading flow, and list formatting before rendering.
- Minor presentation or aesthetics related edits are encouraged. You may have the freedom to:
1. Highlight key phrases or words with supported Markdown emphasis or semantic highlights.
2. Add short allowlisted callouts when they clarify an existing point.
3. Extract key terms (not title) into standalone subheaders.
4. Break down long paragraphs into smaller bullet points/numbered lists.
5. Add visual cues, section dividers, and footnotes with the supported presentation syntax below.

BOUNDARIES:
- Do not access S3. 
- Do not invoke any write tools.
- Do not add new sections or paragraphs.
- Do not invent new product claims, medical claims, or unrelated sections.
- Do not output raw HTML tags such as `<div>`, `<aside>`, `<span>`, `<mark>`, `<hr>`, `<sup>`, `<section>` in `body_markdown`.
- Do not include literal raw newlines or other control characters inside JSON strings.

ALLOWLISTED PRESENTATION SYNTAX:
- Use `==highlighted text==` for a subtle semantic highlight. Do not use `<mark>` or `<span>`.
- Use `---` on its own line for a section divider.
- Use a callout block only in this exact shape:
  `:::callout type="reflection" title="Pause here"\nShort existing idea, rewritten as a concise note.\n:::`
- Supported callout `type` values: `note`, `reflection`, `practice`, `warning`.
- Use footnotes as `A sentence with a note.[^1]` and define them later as `[^1]: Short note text.`

MARKDOWN FORMAT RULES:
- Use `**bolded text**` for bold emphasis. Do not use `<strong>` or `</strong>`.
- Use `*emphasized text*` for italic emphasis. Do not use `<em>` or `</em>`.
- Use `- item text` for unordered bullet lists.
- Use `1. item text`, `2. item text`, etc. for numbered lists.
- Keep each list item on its own line. Do not output `<ul>`, `<ol>`, or `<li>` tags.
- Keep supporting image placeholders exactly as-is, such as `{image_001.jpg}`.
OUTPUT:
Do not add any text before or after the JSON.
Return ONLY valid JSON with:
{
  "body_markdown": "presentation-polished markdown - must be a single valid JSON string",
  "presentation_notes": ["short note"]
}

JSON SAFETY RULES:
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

        numbered_paren_match = re.fullmatch(r"(\d+)\)\s+(.+)", stripped)
        if numbered_paren_match:
            stripped = f"{numbered_paren_match.group(1)}. {numbered_paren_match.group(2)}"

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
