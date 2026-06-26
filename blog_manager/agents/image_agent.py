"""Image subagent for creating local blog cover image artifacts."""

from __future__ import annotations

from dataclasses import replace
import json
import logging
from typing import Any

from blog_manager.config import SUBAGENT_LLM_CONFIG
from blog_manager.schemas import ExpandedPost, LocalArtifact, SupportingImage
from blog_manager.services.llm_client import BlogLlmClient
from blog_manager.tools.image_generation_tool import ImageGenerationTool

logger = logging.getLogger(__name__)

IMAGE_SUBAGENT_PROMPT = """You are image_subagent.

MISSION:
Create local JPEG imagery that visually supports the Entourage blog post.

VALUE-ADDED RESPONSIBILITIES:
- Produce exactly one cover image and all requested supporting images.
- Upgrade the main agent's high-level visual briefs into high-quality generation prompts.
- Add composition, lighting, color palette, visual style, and safety constraints.
- Provide detailed descriptions of depicted scenes/actions/people if applicable.
- Avoid texts, real people names, medical imagery, copyrighted characters, and fear-based visuals.

BOUNDARIES:
- Do not access S3.
- Do not invoke any image generation tools.
- Do not revise the article content.

OUTPUT:
Do not add any text before or after the JSON.
Return ONLY valid JSON with:
{
  "image_count": 2,
  "cover": {
    "enhanced_prompt": "detailed production quality cover image generation prompt"
  },
  "supporting_images": [
    {
      "filename": "image_001.jpg",
      "enhanced_prompt": "detailed production quality supporting image generation prompt"
    }
  ],
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
    ) -> list[LocalArtifact]:
        """Create cover and supporting JPEGs locally after enhancing prompts."""
        enhanced_post, supporting_images, image_count, rationale = await self._prepare_image_prompts(
            post,
            instructions=instructions,
            prior_errors=prior_errors or [],
        )

        cover_artifact = await self.image_tool.create_cover_jpg(enhanced_post)
        cover_artifact.metadata.update(
            {
                "image_agent": "image_subagent",
                "image_count": str(image_count),
                "prompt_enhanced": "true",
                "visual_rationale": rationale,
            }
        )
        artifacts = [cover_artifact]
        for supporting_image in supporting_images:
            artifact = await self.image_tool.create_supporting_jpg(enhanced_post, supporting_image)
            artifact.metadata.update(
                {
                    "image_agent": "image_subagent",
                    "image_count": str(image_count),
                    "prompt_enhanced": "true",
                    "visual_rationale": rationale,
                }
            )
            artifacts.append(artifact)
        return artifacts

    async def _prepare_image_prompts(
        self,
        post: ExpandedPost,
        *,
        instructions: str,
        prior_errors: list[str],
    ) -> tuple[ExpandedPost, list[SupportingImage], int, str]:
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
            enhanced_prompt = _cover_prompt_from_payload(payload)
            if enhanced_prompt:
                image_count = _as_int(payload.get("image_count"), default=1)
                rationale = str(payload.get("visual_rationale") or "").strip()
                supporting_images = _supporting_images_from_payload(payload, post.supporting_images)
                return (
                    replace(post, image_prompt=enhanced_prompt),
                    supporting_images,
                    image_count,
                    rationale,
                )
        except Exception as exc:
            logger.warning("Image subagent brain failed; using deterministic fallback: %s", exc)

        return (
            replace(post, image_prompt=enhance_cover_prompt(post, instructions=instructions)),
            [
                replace(
                    image,
                    prompt=enhance_supporting_prompt(post, image, instructions=instructions),
                )
                for image in post.supporting_images
            ],
            determine_image_count(post, instructions=instructions),
            "deterministic prompt enhancement fallback used",
        )


def determine_image_count(post: ExpandedPost, *, instructions: str = "") -> int:
    """Decide how many images the subagent should generate for this format."""
    _ = instructions
    return 1 + len(post.supporting_images)


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


def enhance_supporting_prompt(
    post: ExpandedPost,
    supporting_image: SupportingImage,
    *,
    instructions: str = "",
) -> str:
    """Turn a supporting visual brief into a production-oriented prompt."""
    instruction_context = instructions.strip()
    parts = [
        f"Supporting image {supporting_image.filename} for an Entourage blog post titled: {post.title.strip()}.",
        f"Core visual brief: {supporting_image.prompt.strip()}",
        "Style: calm, hopeful, modern editorial illustration with soft natural light.",
        "Composition: inline blog illustration, focused scene, no readable text.",
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
            "supporting_images": [
                {
                    "filename": image.filename,
                    "prompt": image.prompt,
                    "alt_text": image.alt_text,
                }
                for image in post.supporting_images
            ],
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _cover_prompt_from_payload(payload: dict[str, Any]) -> str:
    cover = payload.get("cover")
    if isinstance(cover, dict):
        return str(cover.get("enhanced_prompt") or "").strip()
    return str(payload.get("enhanced_prompt") or "").strip()


def _supporting_images_from_payload(
    payload: dict[str, Any],
    existing_images: list[SupportingImage],
) -> list[SupportingImage]:
    raw_items = payload.get("supporting_images")
    if not isinstance(raw_items, list):
        return existing_images
    prompt_by_filename = {
        str(item.get("filename") or "").strip(): str(item.get("enhanced_prompt") or "").strip()
        for item in raw_items
        if isinstance(item, dict)
    }
    return [
        replace(image, prompt=prompt_by_filename.get(image.filename) or image.prompt)
        for image in existing_images
    ]


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
