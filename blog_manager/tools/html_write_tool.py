"""Local-only HTML writer tool for the HTML subagent."""

from __future__ import annotations

from blog_manager.schemas import ExpandedPost, LocalArtifact
from blog_manager.services.local_artifact_service import LocalArtifactService


class HtmlWriteTool:
    """Write static HTML artifacts without S3 access."""

    def __init__(self, artifact_service: LocalArtifactService | None = None):
        self.artifact_service = artifact_service or LocalArtifactService()

    def write_article_html(self, post: ExpandedPost) -> LocalArtifact:
        return self.artifact_service.write_article_html(post)
