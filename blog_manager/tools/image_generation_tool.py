"""Local-only image generation tool for the image subagent."""

from __future__ import annotations

from blog_manager.schemas import ExpandedPost, LocalArtifact
from blog_manager.services.local_artifact_service import LocalArtifactService


class ImageGenerationTool:
    """Create local cover image artifacts without S3 access."""

    def __init__(self, artifact_service: LocalArtifactService | None = None):
        self.artifact_service = artifact_service or LocalArtifactService()

    async def create_cover_jpg(self, post: ExpandedPost) -> LocalArtifact:
        return await self.artifact_service.create_cover_jpg(post)
