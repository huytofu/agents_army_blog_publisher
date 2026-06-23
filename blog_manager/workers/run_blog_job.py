"""One-shot blog publishing worker for CLI and Lambda entrypoints."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
import logging
from typing import Any

from blog_manager.config import BLOG_STORAGE_CONFIG, SERVER_CONFIG, WORKER_CONFIG
from blog_manager.graphs import BlogGenerationWorkflow, build_blog_generation_graph, initial_state
from blog_manager.schemas import BlogGraphState, BlogIdea
from blog_manager.services import S3BlogStore

logger = logging.getLogger(__name__)


GraphRunner = Callable[[BlogIdea], Awaitable[Any]]


@dataclass(frozen=True)
class BlogIdeaJobResult:
    """Result for processing one idea file."""

    key: str
    status: str
    slug: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status,
            "slug": self.slug,
            "errors": self.errors,
        }


@dataclass(frozen=True)
class BlogJobSummary:
    """Structured summary returned by CLI and Lambda worker runs."""

    attempted: int
    published: int
    failed: int
    dry_run: bool
    results: list[BlogIdeaJobResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "published": self.published,
            "failed": self.failed,
            "dry_run": self.dry_run,
            "ok": self.ok,
            "results": [result.to_dict() for result in self.results],
        }


async def run_blog_job(
    *,
    max_ideas: int | None = None,
    dry_run: bool | None = None,
    s3_store: S3BlogStore | None = None,
    graph_runner: GraphRunner | None = None,
) -> BlogJobSummary:
    """Process unprocessed idea files with per-idea failure isolation."""
    resolved_max_ideas = max_ideas or int(WORKER_CONFIG["MAX_IDEAS_PER_RUN"])
    resolved_dry_run = bool(WORKER_CONFIG["DRY_RUN"] if dry_run is None else dry_run)
    store = s3_store or S3BlogStore()

    logger.info(
        "Starting blog publisher worker dry_run=%s max_ideas=%s bucket_configured=%s",
        resolved_dry_run,
        resolved_max_ideas,
        bool(BLOG_STORAGE_CONFIG["S3_BUCKET"]),
    )
    ideas = store.list_unprocessed_ideas(max_items=resolved_max_ideas)
    logger.info("Found %s unprocessed idea(s).", len(ideas))

    runner = graph_runner or _build_graph_runner(
        store=store,
        dry_run=resolved_dry_run,
        max_ideas=resolved_max_ideas,
    )
    results: list[BlogIdeaJobResult] = []

    for idea in ideas:
        try:
            final_state = await runner(idea)
            errors = _state_errors(final_state)
            slug = _state_slug(final_state)
            if errors:
                logger.error("Blog idea failed key=%s errors=%s", idea.key, errors)
                results.append(
                    BlogIdeaJobResult(
                        key=idea.key,
                        status="failed",
                        slug=slug,
                        errors=errors,
                    )
                )
                continue

            status = "dry_run" if resolved_dry_run else "published"
            logger.info("Blog idea completed key=%s status=%s slug=%s", idea.key, status, slug)
            results.append(BlogIdeaJobResult(key=idea.key, status=status, slug=slug))
        except Exception as exc:
            logger.exception("Blog idea crashed key=%s", idea.key)
            results.append(
                BlogIdeaJobResult(
                    key=idea.key,
                    status="failed",
                    errors=[str(exc)],
                )
            )

    failed = sum(1 for result in results if result.status == "failed")
    published = sum(1 for result in results if result.status in {"published", "dry_run"})
    return BlogJobSummary(
        attempted=len(results),
        published=published,
        failed=failed,
        dry_run=resolved_dry_run,
        results=results,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the one-shot worker from a local shell or container command."""
    logging.basicConfig(level=SERVER_CONFIG["LOG_LEVEL"])
    args = _parse_args(argv)
    summary = asyncio.run(
        run_blog_job(
            max_ideas=args.max_ideas,
            dry_run=args.dry_run,
        )
    )
    logger.info("Blog publisher worker summary: %s", summary.to_dict())
    return 0 if summary.ok else 1


def _build_graph_runner(
    *,
    store: S3BlogStore,
    dry_run: bool,
    max_ideas: int,
) -> GraphRunner:
    config = {
        **WORKER_CONFIG,
        "DRY_RUN": dry_run,
        "MAX_IDEAS_PER_RUN": max_ideas,
    }
    workflow = BlogGenerationWorkflow(s3_store=store, config=config)
    graph = build_blog_generation_graph(workflow)

    async def run_graph(idea: BlogIdea) -> BlogGraphState | dict[str, Any]:
        return await graph.ainvoke(initial_state(idea))

    return run_graph


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Entourage blog publisher job.")
    parser.add_argument(
        "--max-ideas",
        type=int,
        default=None,
        help="Maximum unprocessed idea files to process in this run.",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=None,
        help="Run graph validation without S3 writes.",
    )
    parser.add_argument(
        "--publish",
        dest="dry_run",
        action="store_false",
        help="Force publishing even if BLOG_DRY_RUN is enabled.",
    )
    return parser.parse_args(argv)


def _state_errors(state: BlogGraphState | dict[str, Any]) -> list[str]:
    return list(_state_value(state, "errors", []) or [])


def _state_slug(state: BlogGraphState | dict[str, Any]) -> str:
    post = _state_value(state, "expanded_post")
    if isinstance(post, dict):
        return str(post.get("slug") or "")
    return str(getattr(post, "slug", "") or "")


def _state_value(state: BlogGraphState | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(state, dict):
        return state.get(key, default)
    return getattr(state, key, default)


if __name__ == "__main__":
    raise SystemExit(main())
