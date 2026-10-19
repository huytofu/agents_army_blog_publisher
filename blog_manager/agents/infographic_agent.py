"""One-shot Pinterest infographic: prompt spec, then a single image render."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from blog_manager.config import SUBAGENT_LLM_CONFIG
from blog_manager.schemas import ExpandedPost, LocalArtifact
from blog_manager.services.llm_client import BlogLlmClient
from blog_manager.services.local_artifact_service import LocalArtifactService

logger = logging.getLogger(__name__)

_DIAGRAM_TYPES = {"flowchart", "steps", "mindmap", "comparison"}
_MIN_NODES = 3
_MAX_NODES = 6
_MAX_WORDS = 6

INFOGRAPHIC_SUBAGENT_PROMPT = """You are infographic_prompt_agent.

MISSION:
Turn one finalized Entourage blog post into a single Pinterest infographic specification.
The graphic must be easy to read and remember: one flowchart, step sequence, mind map, or comparison.

RESPONSIBILITIES:
- Use only ideas already present in the post, excerpt, and FAQ items.
- Choose exactly one diagram_type: flowchart, steps, mindmap, or comparison.
- Write a title of 2 to 6 words.
- Write 3 to 6 node labels. Each label is 2 to 6 words.
- These strings are the only words the image may contain. They must be exact and memorable.
- Write image_prompt so it quotes the title and every node label verbatim.

BOUNDARIES:
- Do not add claims, statistics, citations, or steps that are not in the source post.
- Do not rewrite the article.
- Do not include URLs, logos, or long sentences.
- Do not decide publishing, retries, or failure handling.

OUTPUT:
Do not add any text before or after the JSON.
Return ONLY valid JSON with exactly these fields:
{
  "diagram_type": "flowchart",
  "title": "short exact title",
  "nodes": [
    {"label": "short exact label"}
  ],
  "image_prompt": "infographic prompt that quotes the title and every label exactly"
}
"""


class InfographicAgentError(RuntimeError):
    """Raised when the one-shot infographic prompt or render cannot be completed."""


@dataclass(frozen=True)
class _GraphicSpec:
    diagram_type: str
    title: str
    labels: list[str]
    image_prompt: str


class InfographicAgent:
    """Agent A writes the graphic spec. Agent B renders it once."""

    def __init__(
        self,
        llm_client: BlogLlmClient | None = None,
        artifact_service: LocalArtifactService | None = None,
    ):
        self.llm_client = llm_client or BlogLlmClient(config=SUBAGENT_LLM_CONFIG)
        self.artifact_service = artifact_service or LocalArtifactService()

    async def create_infographic(self, post: ExpandedPost) -> LocalArtifact:
        """Compile one graphic prompt, then render one Pinterest JPEG."""
        raw = await self.llm_client.chat_completion(
            [
                {"role": "system", "content": INFOGRAPHIC_SUBAGENT_PROMPT},
                {"role": "user", "content": _build_user_prompt(post)},
            ]
        )
        spec = _spec_from_response(raw)
        prompt = _render_prompt(spec)
        artifact = await self.artifact_service.create_pinterest_jpg(post, prompt=prompt)
        artifact.metadata.update(
            {
                "infographic_agent": "infographic_prompt_agent",
                "diagram_type": spec.diagram_type,
                "title": spec.title,
            }
        )
        return artifact


def _build_user_prompt(post: ExpandedPost) -> str:
    payload = {
        "title": post.title,
        "excerpt": post.excerpt,
        "body_markdown": post.body_markdown,
        "faq_items": post.faq_items,
        "safety_notes": post.safety_notes,
        "primary_keyword": post.primary_keyword,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _spec_from_response(raw: str) -> _GraphicSpec:
    payload = _parse_json_object(raw)
    diagram_type = str(payload.get("diagram_type") or "").strip().lower()
    if diagram_type not in _DIAGRAM_TYPES:
        raise InfographicAgentError(
            "Infographic diagram_type must be flowchart, steps, mindmap, or comparison."
        )

    title = _exact_label(payload.get("title"), field_name="title")
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list):
        raise InfographicAgentError("Infographic nodes must be a list.")

    labels: list[str] = []
    seen: set[str] = set()
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue
        label = _exact_label(item.get("label"), field_name="node label")
        key = label.casefold()
        if key in seen or key == title.casefold():
            continue
        seen.add(key)
        labels.append(label)

    if not _MIN_NODES <= len(labels) <= _MAX_NODES:
        raise InfographicAgentError(
            f"Infographic must include {_MIN_NODES} to {_MAX_NODES} node labels."
        )

    image_prompt = str(payload.get("image_prompt") or "").strip()
    return _GraphicSpec(
        diagram_type=diagram_type,
        title=title,
        labels=labels,
        image_prompt=image_prompt,
    )


def _exact_label(value: Any, *, field_name: str) -> str:
    label = re.sub(r"\s+", " ", str(value or "").strip())
    word_count = len(label.split())
    if word_count < 2 or word_count > _MAX_WORDS:
        raise InfographicAgentError(
            f"Infographic {field_name} must be 2 to {_MAX_WORDS} words."
        )
    return label


def _render_prompt(spec: _GraphicSpec) -> str:
    exact_strings = [spec.title, *spec.labels]
    body = spec.image_prompt
    if not body or any(label not in body for label in exact_strings):
        steps = " ".join(
            f'Step {index} exactly: "{label}".' for index, label in enumerate(spec.labels, start=1)
        )
        body = f'{spec.diagram_type} infographic. Title exactly: "{spec.title}". {steps}'

    quoted = ", ".join(f'"{label}"' for label in exact_strings)
    return (
        f"{body} "
        "Canvas: vertical Pinterest pin, 2:3 aspect ratio, 1024x1536. "
        f"Layout: one {spec.diagram_type} with large readable sans-serif text and clear spacing. "
        "Palette: soothing greens, warm neutrals, soft indigo, high contrast. "
        f"Render these strings exactly and do not add any other words: {quoted}."
    )


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        parsed = _parse_embedded_json_object(text)
        if parsed is None:
            raise InfographicAgentError(f"Infographic JSON parse failed: {exc}") from exc
    if not isinstance(parsed, dict):
        raise InfographicAgentError("Infographic output must be a JSON object.")
    return parsed


def _parse_embedded_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            parsed, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
