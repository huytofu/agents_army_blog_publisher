"""Local artifact tools used by blog publisher subagents.

This module intentionally has no S3 access. Subagents can write local files and
return artifact descriptors; the main pipeline owns S3 upload and cleanup.
"""

from __future__ import annotations

import base64
import html
import asyncio
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Protocol
from urllib.parse import quote
from urllib.request import Request, urlopen

from blog_manager.config import BLOG_STORAGE_CONFIG, IMAGE_CONFIG
from blog_manager.constants import (
    COVER_IMAGE_CONTENT_TYPE,
    COVER_IMAGE_FILENAME,
    POST_HTML_CONTENT_TYPE,
    POST_HTML_FILENAME,
)
from blog_manager.schemas import ExpandedPost, LocalArtifact, SupportingImage
from blog_manager.services.idea_parser import slugify

try:
    from together import Together  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional until image provider is enabled
    Together = None

logger = logging.getLogger(__name__)

_PLACEHOLDER_JPEG_BASE64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////"
    "//////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/"
    "9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/ASP/xAAUEQE"
    "AAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/ASP/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Al//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACA"
    "EBAAE/IV//2gAMAwEAAgADAAAAEP/EFBQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EFBQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH"
    "//EFBABAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8QH//Z"
)


class LocalArtifactError(RuntimeError):
    """Raised when a local artifact tool cannot complete safely."""


class ImageProvider(Protocol):
    """Provider interface for generating JPEG bytes from a prompt."""

    async def generate_jpeg(self, *, prompt: str, width: int, height: int) -> bytes:
        ...


class ConfiguredImageProvider:
    """Configurable image provider wrapper.

    `BLOG_IMAGE_PROVIDER=together` uses Together's image API. `placeholder`
    returns a tiny valid JPEG for dry-run plumbing.
    """

    def __init__(self, config: dict | None = None):
        self.config = config or IMAGE_CONFIG

    async def generate_jpeg(self, *, prompt: str, width: int, height: int) -> bytes:
        provider = str(self.config.get("PROVIDER") or "").strip().lower()
        if provider == "placeholder":
            return _default_image_bytes()
        if provider == "together":
            return await asyncio.to_thread(self._generate_together_image, prompt=prompt)
        raise LocalArtifactError(
            "BLOG_IMAGE_PROVIDER is not configured with an implemented image provider."
        )

    def _generate_together_image(self, *, prompt: str) -> bytes:
        if Together is None:
            raise LocalArtifactError("together is required for BLOG_IMAGE_PROVIDER=together.")
        model = str(self.config.get("MODEL") or "").strip()
        if not model:
            raise LocalArtifactError("BLOG_IMAGE_MODEL is required for Together image generation.")

        api_key = str(self.config.get("API_KEY") or "").strip()
        client = Together(api_key=api_key) if api_key else Together()
        try:
            response = client.images.generate(
                prompt=prompt,
                model=model,
                response_format="base64",
            )
        except TypeError:
            response = client.images.generate(prompt=prompt, model=model)
        data_item = _first_response_item(response)

        image_bytes = _image_bytes_from_b64(data_item)
        if image_bytes:
            return image_bytes

        image_bytes = self._image_bytes_from_url(data_item)
        if image_bytes:
            return image_bytes

        logger.warning("Together image response did not include usable b64_json or url; using default image.")
        return _default_image_bytes()

    def _image_bytes_from_url(self, data_item: object | None) -> bytes:
        url = str(getattr(data_item, "url", "") or "").strip()
        if not url:
            return b""
        timeout = int(self.config.get("TIMEOUT_SEC") or IMAGE_CONFIG["TIMEOUT_SEC"])
        request = Request(
            url,
            headers={"User-Agent": "entourage-blog-publisher/1.0"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            logger.warning("Together image URL download failed: %s", exc)
            return b""


class LocalArtifactService:
    """Local-only tool service for HTML and image artifacts."""

    def __init__(
        self,
        work_root: str | Path | None = None,
        image_provider: ImageProvider | None = None,
    ):
        self.work_root = Path(work_root or BLOG_STORAGE_CONFIG["LOCAL_WORK_ROOT"]).resolve()
        self.image_provider = image_provider or ConfiguredImageProvider()

    def write_article_html(self, post: ExpandedPost) -> LocalArtifact:
        """Render and write a static article HTML file under the post slug."""
        slug = _validate_slug(post.slug)
        output_path = self._post_dir(slug) / POST_HTML_FILENAME
        html_text = render_article_html(post)
        _write_text(output_path, html_text)
        return LocalArtifact(
            local_path=str(output_path),
            relative_key=f"blog/{slug}/{POST_HTML_FILENAME}",
            content_type=POST_HTML_CONTENT_TYPE,
            metadata={"slug": slug, "artifact_type": "html"},
        )

    async def create_cover_jpg(self, post: ExpandedPost) -> LocalArtifact:
        """Generate and write a local JPEG cover image under the post slug."""
        slug = _validate_slug(post.slug)
        output_path = self._post_dir(slug) / COVER_IMAGE_FILENAME
        image_bytes = await self.image_provider.generate_jpeg(
            prompt=post.image_prompt,
            width=IMAGE_CONFIG["WIDTH"],
            height=IMAGE_CONFIG["HEIGHT"],
        )
        _validate_jpeg(image_bytes)
        _write_bytes(output_path, image_bytes)
        return LocalArtifact(
            local_path=str(output_path),
            relative_key=f"blog/{slug}/{COVER_IMAGE_FILENAME}",
            content_type=COVER_IMAGE_CONTENT_TYPE,
            metadata={"slug": slug, "artifact_type": "cover_image"},
        )

    async def create_supporting_jpg(
        self,
        post: ExpandedPost,
        supporting_image: SupportingImage,
    ) -> LocalArtifact:
        """Generate and write one local supporting JPEG image under the post slug."""
        slug = _validate_slug(post.slug)
        filename = _validate_supporting_image_filename(supporting_image.filename)
        output_path = self._post_dir(slug) / filename
        image_bytes = await self.image_provider.generate_jpeg(
            prompt=supporting_image.prompt,
            width=IMAGE_CONFIG["WIDTH"],
            height=IMAGE_CONFIG["HEIGHT"],
        )
        _validate_jpeg(image_bytes)
        _write_bytes(output_path, image_bytes)
        return LocalArtifact(
            local_path=str(output_path),
            relative_key=f"blog/{slug}/{filename}",
            content_type=COVER_IMAGE_CONTENT_TYPE,
            metadata={
                "slug": slug,
                "artifact_type": "supporting_image",
                "filename": filename,
                "alt_text": supporting_image.alt_text,
            },
        )

    def clear_post_artifacts(self, slug: str) -> None:
        """Delete all local artifacts for one post slug.

        This is intended for the main pipeline after confirming S3 uploads.
        Subagents should not call cleanup.
        """
        post_dir = self._post_dir(_validate_slug(slug))
        if post_dir.exists():
            shutil.rmtree(post_dir)

    def clear_artifact(self, artifact: LocalArtifact) -> None:
        """Delete a single local artifact after upload confirmation."""
        path = self._safe_path(artifact.local_path)
        if path.exists():
            path.unlink()

    def validate_artifact(
        self,
        artifact: LocalArtifact | None,
        *,
        slug: str,
        filename: str,
        content_type: str,
    ) -> list[str]:
        """Return validation errors for a local artifact descriptor."""
        errors: list[str] = []
        if artifact is None:
            return ["Artifact is missing."]

        expected_key = f"blog/{_validate_slug(slug)}/{filename}"
        if artifact.relative_key != expected_key:
            errors.append(
                f"Artifact key mismatch: expected {expected_key}, got {artifact.relative_key}."
            )
        if artifact.content_type != content_type:
            errors.append(
                f"Artifact content type mismatch: expected {content_type}, got {artifact.content_type}."
            )

        try:
            path = self._safe_path(artifact.local_path)
        except LocalArtifactError as exc:
            return [*errors, str(exc)]

        if not path.is_file():
            errors.append(f"Artifact file does not exist: {path}.")
        return errors

    def _post_dir(self, slug: str) -> Path:
        path = self._safe_path(self.work_root / "blog" / slug)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _safe_path(self, path: str | Path) -> Path:
        resolved = Path(path).resolve()
        if self.work_root != resolved and self.work_root not in resolved.parents:
            raise LocalArtifactError(f"Path escapes local work root: {resolved}")
        return resolved


def render_article_html(post: ExpandedPost) -> str:
    """Render a complete static HTML article from expanded post content."""
    title = html.escape(post.title)
    excerpt = html.escape(post.excerpt)
    seo_title = html.escape(post.seo_title or post.title)
    seo_description = html.escape(post.seo_description or post.excerpt)
    body = markdown_to_html(post.body_markdown, supporting_images=post.supporting_images)
    cover_path = html.escape(f"cover.jpg")
    article_tags = _article_tags(post)
    article_meta_tags = "\n".join(
        f'    <meta property="article:tag" content="{html.escape(tag)}">'
        for tag in article_tags
    )
    structured_data = _structured_data_scripts(post)
    content_note = _render_safety_notes(post.safety_notes)
    references = _render_citation_suggestions(post.citation_suggestions)
    growth_sections = _render_growth_sections(post)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{seo_title}</title>
    <meta name="description" content="{seo_description}">
    <meta property="og:type" content="article">
    <meta property="og:title" content="{seo_title}">
    <meta property="og:description" content="{seo_description}">
    <meta property="og:image" content="{cover_path}">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{seo_title}">
    <meta name="twitter:description" content="{seo_description}">
    <meta name="twitter:image" content="{cover_path}">
    <meta property="article:published_time" content="{html.escape(post.date)}">
    <link rel="alternate" type="application/rss+xml" title="ENTOURAGE Blog" href="../rss.xml">
{article_meta_tags}
{structured_data}
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; line-height: 1.7; color: #1f2937; margin: 0; background: #f9fafb; }}
        main {{ max-width: 800px; margin: 0 auto; padding: 2rem 1.25rem 4rem; background: #ffffff; }}
        .cover {{ width: 100%; border-radius: 16px; margin: 1.5rem 0; object-fit: cover; }}
        .supporting-figure {{ margin: 1.5rem auto; max-width: 520px; }}
        .supporting-image {{ display: block; width: 100%; max-width: 100%; max-height: 360px; border-radius: 12px; object-fit: cover; }}
        .meta {{ color: #6b7280; font-size: 0.95rem; }}
        .content-note {{ background: #fff7ed; border-left: 4px solid #f97316; border-radius: 12px; margin-top: 2rem; padding: 1rem 1.25rem; }}
        .reference-list {{ background: #f3f4f6; border-radius: 12px; margin-top: 2rem; padding: 1rem 1.25rem; }}
        .subscribe-cta, .comments-section, .related-posts, .share-actions {{ background: #eef2ff; border-radius: 14px; margin-top: 2rem; padding: 1rem 1.25rem; }}
        .comments-section {{ background: #f9fafb; border: 1px solid #e5e7eb; }}
        .share-actions a {{ display: inline-block; margin-right: 0.75rem; }}
        h1, h2, h3 {{ color: #111827; line-height: 1.25; }}
        p {{ margin: 1rem 0; }}
        a {{ color: #4f46e5; }}
        blockquote {{ border-left: 4px solid #6366f1; margin: 1.5rem 0; padding-left: 1rem; color: #4b5563; }}
    </style>
</head>
<body>
    <main>
        <p><a href="../../blogs.html">Back to Blog</a></p>
        <article>
            <p class="meta">{html.escape(post.date)}</p>
            <h1>{title}</h1>
            <p><strong>{excerpt}</strong></p>
            <img class="cover" src="{cover_path}" alt="{title}">
            {body}
            {content_note}
            {references}
        </article>
        {growth_sections}
    </main>
</body>
</html>
"""


def markdown_to_html(
    markdown: str,
    *,
    supporting_images: list[SupportingImage] | None = None,
) -> str:
    """Convert a conservative Markdown subset into static HTML."""
    blocks: list[str] = []
    list_items: list[str] = []
    supporting_image_by_filename = {
        image.filename: image for image in supporting_images or []
    }

    def flush_list() -> None:
        if list_items:
            blocks.append("<ul>" + "".join(list_items) + "</ul>")
            list_items.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            flush_list()
            continue
        placeholder_match = re.fullmatch(r"\{(image_\d{3}\.jpg)\}", line)
        if placeholder_match and placeholder_match.group(1) in supporting_image_by_filename:
            flush_list()
            image = supporting_image_by_filename[placeholder_match.group(1)]
            filename = html.escape(image.filename)
            alt_text = html.escape(image.alt_text)
            blocks.append(
                '<figure class="supporting-figure">'
                f'<img class="supporting-image" src="{filename}" alt="{alt_text}">'
                "</figure>"
            )
            continue
        if line.startswith("### "):
            flush_list()
            blocks.append(f"<h3>{_inline_markdown(line[4:])}</h3>")
        elif line.startswith("## "):
            flush_list()
            blocks.append(f"<h2>{_inline_markdown(line[3:])}</h2>")
        elif line.startswith("# "):
            flush_list()
            blocks.append(f"<h2>{_inline_markdown(line[2:])}</h2>")
        elif line.startswith("> "):
            flush_list()
            blocks.append(f"<blockquote>{_inline_markdown(line[2:])}</blockquote>")
        elif line.startswith("- "):
            list_items.append(f"<li>{_inline_markdown(line[2:])}</li>")
        else:
            flush_list()
            blocks.append(f"<p>{_inline_markdown(line)}</p>")

    flush_list()
    return "\n            ".join(blocks)


def _inline_markdown(value: str) -> str:
    escaped = html.escape(value)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
    return escaped


def _article_tags(post: ExpandedPost) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for item in post.tags:
        tag = str(item or "").strip()
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
    return tags


def _structured_data_scripts(post: ExpandedPost) -> str:
    scripts = [_json_ld_script(_blog_posting_schema(post))]
    faq_schema = _faq_page_schema(post)
    if faq_schema:
        scripts.append(_json_ld_script(faq_schema))
    return "\n".join(scripts)


def _blog_posting_schema(post: ExpandedPost) -> dict[str, object]:
    keywords = _article_tags(post)
    return {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": post.seo_title or post.title,
        "description": post.seo_description or post.excerpt,
        "datePublished": post.date,
        "image": f"blog/{post.slug}/cover.jpg",
        "mainEntityOfPage": f"blog/{post.slug}/index.html",
        "keywords": keywords,
        "articleSection": post.category,
    }


def _faq_page_schema(post: ExpandedPost) -> dict[str, object] | None:
    questions = []
    for item in post.faq_items:
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if not question or not answer:
            continue
        questions.append(
            {
                "@type": "Question",
                "name": question,
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": answer,
                },
            }
        )
    if not questions:
        return None
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": questions,
    }


def _json_ld_script(payload: dict[str, object]) -> str:
    json_text = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return f'    <script type="application/ld+json">\n{json_text}\n    </script>'


def _render_safety_notes(safety_notes: list[str]) -> str:
    items = [str(item or "").strip() for item in safety_notes if str(item or "").strip()]
    if not items:
        return ""
    list_items = "".join(f"<li>{html.escape(item)}</li>" for item in items)
    return (
        '<section class="content-note">'
        "<h2>Content note</h2>"
        "<ul>"
        f"{list_items}"
        "</ul>"
        "</section>"
    )


def _render_citation_suggestions(citation_suggestions: list[str]) -> str:
    items = [str(item or "").strip() for item in citation_suggestions if str(item or "").strip()]
    if not items:
        return ""
    list_items = "".join(f"<li>{html.escape(item)}</li>" for item in items)
    return (
        '<section class="reference-list">'
        "<h2>References worth exploring</h2>"
        "<ul>"
        f"{list_items}"
        "</ul>"
        "</section>"
    )


def _render_growth_sections(post: ExpandedPost) -> str:
    title = html.escape(post.title)
    slug = html.escape(post.slug)
    subject = quote(post.title)
    article_path = quote(f"https://www.entourage-ai.life/blog/{post.slug}/index.html", safe=":/")
    return f"""
        <section class="subscribe-cta">
            <h2>Get the weekly highlight</h2>
            <p>Get one weekly highlight, no spam. We send the strongest reflection from the ENTOURAGE blog each week.</p>
            <form data-blog-api-placeholder="subscribe" action="#" method="post">
                <label for="subscriber-email">Email address</label>
                <input id="subscriber-email" name="email" type="email" placeholder="you@example.com" autocomplete="email">
                <button type="submit">Subscribe</button>
            </form>
        </section>
        <section id="comments" class="comments-section" data-blog-api-placeholder="comments" data-post-slug="{slug}">
            <h2>Join the conversation</h2>
            <p>What did this bring up for you? Sign in to leave a moderated comment once the blog API is connected.</p>
            <div class="comments-list" aria-live="polite"></div>
        </section>
        <section class="related-posts" data-related-category="{html.escape(post.category)}" data-related-tags="{html.escape(','.join(post.tags))}">
            <h2>Related reflections</h2>
            <p>Related posts will be selected from the same category and tags in <code>blog/posts.json</code>.</p>
        </section>
        <section class="share-actions">
            <h2>Share this post</h2>
            <a href="mailto:?subject={subject}&body={article_path}">Share by email</a>
            <a href="https://www.linkedin.com/sharing/share-offsite/?url={article_path}">Share on LinkedIn</a>
            <a href="https://twitter.com/intent/tweet?text={subject}&url={article_path}">Share on X</a>
        </section>
        <section class="comments-section">
            <h2>Reflection prompt</h2>
            <p>Which sentence from “{title}” feels useful enough to test this week?</p>
        </section>
    """


def _first_response_item(response: object) -> object | None:
    data = getattr(response, "data", None)
    if not data:
        return None
    try:
        return data[0]
    except (IndexError, TypeError):
        return None


def _image_bytes_from_b64(data_item: object | None) -> bytes:
    encoded = str(getattr(data_item, "b64_json", "") or "").strip()
    if not encoded:
        return b""
    try:
        return base64.b64decode(encoded)
    except Exception as exc:
        logger.warning("Together image b64_json decode failed: %s", exc)
        return b""


def _default_image_bytes() -> bytes:
    padded = _PLACEHOLDER_JPEG_BASE64 + "=" * (-len(_PLACEHOLDER_JPEG_BASE64) % 4)
    try:
        return base64.b64decode(padded)
    except Exception:
        # Minimal JPEG SOI/EOI bytes keep the pipeline moving if the embedded
        # placeholder is ever malformed.
        return b"\xff\xd8\xff\xd9"


def _validate_slug(slug: str) -> str:
    safe_slug = slugify(slug)
    if safe_slug != slug:
        raise LocalArtifactError(f"Invalid post slug: {slug}")
    return safe_slug


def _validate_supporting_image_filename(filename: str) -> str:
    safe_filename = filename.strip()
    if not re.fullmatch(r"image_\d{3}\.jpg", safe_filename):
        raise LocalArtifactError(f"Invalid supporting image filename: {filename}")
    return safe_filename


def _validate_jpeg(image_bytes: bytes) -> None:
    if not image_bytes.startswith(b"\xff\xd8"):
        raise LocalArtifactError("Image provider did not return JPEG bytes.")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
