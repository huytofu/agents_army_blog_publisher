"""Image subagent for creating local blog cover image artifacts."""

from __future__ import annotations

from dataclasses import replace
import json
import logging
from typing import Any

from blog_manager.config import SUBAGENT_LLM_CONFIG
from blog_manager.schemas import ExpandedPost, LocalArtifact
from blog_manager.services.llm_client import BlogLlmClient
from blog_manager.tools.image_generation_tool import ImageGenerationTool

logger = logging.getLogger(__name__)

IMAGE_SUBAGENT_PROMPT = """You are image_subagent.

MISSION:
Create local JPEG cover imagery that visually supports the Entourage blog post.

VALUE-ADDED RESPONSIBILITIES:
- Produce exactly one cover image.
- Upgrade the main agent's high-level visual brief into a high-quality generation prompt.
- Add composition, lighting, color palette, visual style, and safety constraints.
- Provide detailed descriptions of depicted scenes/actions/people if applicable.
- Avoid texts, real people names, medical imagery, copyrighted characters, and fear-based visuals.

BOUNDARIES:
- Do not access S3.
- Do not revise the article content.
- Return only JSON for the preparation step. The graph will invoke the local image tool after your preparation.

OUTPUT:
Return ONLY valid JSON with:
{
  "image_count": 1,
  "enhanced_prompt": "production quality image generation prompt",
  "visual_rationale": "short reason"
}
"""


class ImageAgent:
    """Subagent facade for generating local cover image files."""

    def __init__(
        self,
        image_tool: ImageGenerationTool | None = None,
        llm_client: BlogLlmClient | None = None,
    ):
        self.image_tool = image_tool or ImageGenerationTool()
        self.llm_client = llm_client or BlogLlmClient(config=SUBAGENT_LLM_CONFIG)

    async def create_image_artifact(
        self,
        post: ExpandedPost,
        *,
        instructions: str = "",
        prior_errors: list[str] | None = None,
    ) -> LocalArtifact:
        """Create `cover.jpg` locally after enhancing the image prompt."""
        enhanced_post, image_count, rationale = await self._prepare_image_prompt(
            post,
            instructions=instructions,
            prior_errors=prior_errors or [],
        )
        if image_count != 1:
            # The current website contract supports one cover image per post.
            image_count = 1

        artifact = await self.image_tool.create_cover_jpg(enhanced_post)
        artifact.metadata.update(
            {
                "image_agent": "image_subagent",
                "image_count": str(image_count),
                "prompt_enhanced": "true",
                "visual_rationale": rationale,
            }
        )
        return artifact

    async def _prepare_image_prompt(
        self,
        post: ExpandedPost,
        *,
        instructions: str,
        prior_errors: list[str],
    ) -> tuple[ExpandedPost, int, str]:
        try:
            raw = await self.llm_client.chat_completion(
                [
                    {"role": "system", "content": IMAGE_SUBAGENT_PROMPT},
                    {
                        "role": "user",
                        "content": _build_image_user_prompt(
                            post,
                            instructions=instructions,
                            prior_errors=prior_errors,
                        ),
                    },
                ]
            )
            payload = _parse_json_object(raw)
            enhanced_prompt = str(payload.get("enhanced_prompt") or "").strip()
            if enhanced_prompt:
                image_count = _as_int(payload.get("image_count"), default=1)
                rationale = str(payload.get("visual_rationale") or "").strip()
                return replace(post, image_prompt=enhanced_prompt), image_count, rationale
        except Exception as exc:
            logger.warning("Image subagent brain failed; using deterministic fallback: %s", exc)

        return (
            replace(post, image_prompt=enhance_cover_prompt(post, instructions=instructions)),
            determine_image_count(post, instructions=instructions),
            "deterministic prompt enhancement fallback used",
        )


def determine_image_count(post: ExpandedPost, *, instructions: str = "") -> int:
    """Decide how many images the subagent should generate for this format."""
    # The current `posts.json`/static website contract supports one cover image.
    _ = post
    _ = instructions
    return 1


def enhance_cover_prompt(post: ExpandedPost, *, instructions: str = "") -> str:
    """Turn the main agent's visual brief into a production-oriented prompt."""
    base_prompt = post.image_prompt.strip()
    title_context = post.title.strip()
    instruction_context = instructions.strip()

    parts = [
        f"Cover image for an Entourage blog post titled: {title_context}.",
        f"Core visual brief: {base_prompt}",
        "Style: calm, hopeful, modern editorial illustration with soft natural light.",
        "Composition: wide 1200x630 cover, strong focal point, balanced negative space, no text.",
        "Palette: soothing greens, warm neutrals, soft indigo accents, gentle contrast.",
        "Mood: emotionally grounded, reflective, supportive, growth-oriented.",
        "Avoid: readable text, logos, recognizable real people, medical equipment, clinical settings, copyrighted characters, fear-based imagery.",
    ]
    if instruction_context:
        parts.append(f"Orchestrator emphasis: {instruction_context}")
    return " ".join(parts)


def _build_image_user_prompt(
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
            "image_prompt": post.image_prompt,
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
        raise ValueError("Image subagent output must be a JSON object.")
    return parsed


def _as_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
