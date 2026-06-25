"""Data contracts for blog idea files, feed entries, and local artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BlogIdea:
    """Parsed S3 idea Markdown document."""

    key: str
    frontmatter: dict[str, Any]
    body: str
    raw_text: str

    @property
    def processed(self) -> bool:
        value = self.frontmatter.get("processed", False)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)


@dataclass(frozen=True)
class FeedEntry:
    """Metadata entry consumed by `website/blogs.html`."""

    slug: str
    title: str
    date: str
    excerpt: str
    coverImage: str
    contentPath: str
    supportingImages: list[dict[str, str]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    category: str = "Unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "date": self.date,
            "excerpt": self.excerpt,
            "coverImage": self.coverImage,
            "contentPath": self.contentPath,
            "supportingImages": self.supportingImages,
            "tags": self.tags,
            "category": self.category,
        }


@dataclass(frozen=True)
class SupportingImage:
    """Structured supporting image requested by an expanded post."""

    filename: str
    prompt: str
    alt_text: str

    def to_feed_image(self, *, slug: str) -> dict[str, str]:
        return {
            "filename": self.filename,
            "path": f"blog/{slug}/{self.filename}",
            "altText": self.alt_text,
        }


@dataclass(frozen=True)
class ExpandedPost:
    """Structured output from the main expansion/orchestrator agent."""

    title: str
    slug: str
    date: str
    excerpt: str
    body_markdown: str
    image_prompt: str
    seo_title: str = ""
    seo_description: str = ""
    primary_keyword: str = ""
    search_intent: str = "unknown"
    faq_items: list[dict[str, str]] = field(default_factory=list)
    citation_suggestions: list[str] = field(default_factory=list)
    safety_notes: list[str] = field(default_factory=list)
    supporting_images: list[SupportingImage] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    category: str = "Unknown"

    def to_feed_entry(self) -> FeedEntry:
        return FeedEntry(
            slug=self.slug,
            title=self.title,
            date=self.date,
            excerpt=self.excerpt,
            coverImage=f"blog/{self.slug}/cover.jpg",
            contentPath=f"blog/{self.slug}/index.html",
            supportingImages=[
                image.to_feed_image(slug=self.slug) for image in self.supporting_images
            ],
            tags=self.tags,
            category=self.category,
        )


@dataclass(frozen=True)
class LocalArtifact:
    """Local file prepared by a subagent for main-flow S3 publishing.

    `relative_key` is the destination S3 object key relative to the website
    bucket root, such as `blog/my-post/index.html` or `blog/my-post/cover.jpg`.
    It is not a separate parent blog post ID, but the post slug can be inferred
    from this path because generated artifacts live under `blog/<slug>/...`.
    """

    local_path: str
    relative_key: str
    content_type: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentInvocation:
    """Instruction for a later workflow node to invoke a named subagent.

    `instructions` is the complete prompt payload the orchestrator wants the
    workflow to pass to that subagent. It should describe the assignment and
    expected local artifact, without assuming the subagent's internal tools.
    """

    name: str
    purpose: str
    instructions: str


@dataclass(frozen=True)
class BlogAgentResult:
    """Content result returned by the blog expansion agent."""

    post: ExpandedPost
    raw_response: str = ""


@dataclass(frozen=True)
class BlogPipelineDecision:
    """Strict decision returned by the ReAct-style pipeline supervisor."""

    decision: str
    reason: str
    content_revision_instruction: str = ""
    artifact_retry_instruction: str = ""
    subagent_plan: list[AgentInvocation] = field(default_factory=list)
    raw_response: str = ""


@dataclass
class HtmlArtifactState:
    """Retryable state used inside the HTML artifact subgraph."""

    expanded_post: ExpandedPost
    instructions: str
    artifact: LocalArtifact | None = None
    retry_count: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class ImageArtifactState:
    """Retryable state used inside the image artifact subgraph."""

    expanded_post: ExpandedPost
    instructions: str
    artifacts: list[LocalArtifact] = field(default_factory=list)
    retry_count: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class BlogGraphState:
    """Shared state shape for the future LangGraph blog generation workflow."""

    idea: BlogIdea | None = None
    expanded_post: ExpandedPost | None = None
    feed_entry: FeedEntry | None = None
    html_artifact: LocalArtifact | None = None
    image_artifact: LocalArtifact | None = None
    image_artifacts: list[LocalArtifact] = field(default_factory=list)
    subagent_plan: list[AgentInvocation] = field(default_factory=list)
    main_decision: BlogPipelineDecision | None = None
    main_round: int = 0
    artifact_round: int = 0
    html_retry_count: int = 0
    image_retry_count: int = 0
    publish_ready: bool = False
    errors: list[str] = field(default_factory=list)
