"""HTML subagent for creating local static blog article artifacts."""

from __future__ import annotations

from dataclasses import replace
import re

from blog_manager.schemas import ExpandedPost, LocalArtifact
from blog_manager.tools.html_write_tool import HtmlWriteTool

HTML_SUBAGENT_PROMPT = """You are html_subagent.

MISSION:
Create a local static HTML article artifact that reads well on the Entourage blog.

VALUE-ADDED RESPONSIBILITIES:
- Improve web readability & accessibility without changing the core meaning of the post.
- Normalize paragraph spacing, heading flow, and list formatting before rendering.
- Preserve the main agent's words unless a tiny presentation edit is needed.

BOUNDARIES:
- Do not access S3.
- Do not invent new product claims, medical claims, or unrelated sections.
- Return the local artifact descriptor after writing the file.
"""


class HtmlAgent:
    """Subagent facade for converting expanded posts into local HTML files."""

    def __init__(self, html_tool: HtmlWriteTool | None = None):
        self.html_tool = html_tool or HtmlWriteTool()

    def create_html_artifact(
        self,
        post: ExpandedPost,
        *,
        instructions: str = "",
    ) -> LocalArtifact:
        """Create `index.html` locally after a presentation polish pass."""
        polished_post = _prepare_post_for_html(post, instructions=instructions)
        artifact = self.html_tool.write_article_html(polished_post)
        artifact.metadata.update(
            {
                "presentation_agent": "html_subagent",
                "presentation_pass": "readability_and_layout",
            }
        )
        return artifact


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
