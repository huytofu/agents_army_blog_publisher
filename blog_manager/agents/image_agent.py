"""Image subagent for creating local blog cover image artifacts."""

from __future__ import annotations

from dataclasses import replace

from blog_manager.schemas import ExpandedPost, LocalArtifact
from blog_manager.tools.image_generation_tool import ImageGenerationTool

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
- Return the local artifact descriptor after writing the file.
"""


class ImageAgent:
    """Subagent facade for generating local cover image files."""

    def __init__(self, image_tool: ImageGenerationTool | None = None):
        self.image_tool = image_tool or ImageGenerationTool()

    async def create_image_artifact(
        self,
        post: ExpandedPost,
        *,
        instructions: str = "",
    ) -> LocalArtifact:
        """Create `cover.jpg` locally after enhancing the image prompt."""
        image_count = determine_image_count(post, instructions=instructions)
        if image_count != 1:
            # The current website contract supports one cover image per post.
            image_count = 1

        enhanced_post = replace(
            post,
            image_prompt=enhance_cover_prompt(post, instructions=instructions),
        )
        artifact = await self.image_tool.create_cover_jpg(enhanced_post)
        artifact.metadata.update(
            {
                "image_agent": "image_subagent",
                "image_count": str(image_count),
                "prompt_enhanced": "true",
            }
        )
        return artifact


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
