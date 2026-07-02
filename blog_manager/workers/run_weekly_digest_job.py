"""One-shot weekly digest worker for CLI and Lambda entrypoints."""

from __future__ import annotations

import json
import logging
from typing import Any

from blog_manager.api.config import BlogApiSettings
from blog_manager.api.database import build_mongo_repository
from blog_manager.api.repositories import BlogRepository
from blog_manager.api.weekly_email_worker import run_weekly_highlight_email_job
from blog_manager.config import BLOG_STORAGE_CONFIG, SERVER_CONFIG
from blog_manager.services import S3BlogStore
from blog_manager.services.s3_blog_store import BlogStoreError, _is_s3_not_found

logger = logging.getLogger(__name__)


class WeeklyDigestJobError(RuntimeError):
    """Raised when the weekly digest worker cannot complete."""


def run_weekly_digest_job(
    *,
    s3_store: S3BlogStore | None = None,
    repository: BlogRepository | None = None,
    settings: BlogApiSettings | None = None,
) -> dict[str, int]:
    """Load the weekly highlight artifact and send digest emails to subscribers."""
    store = s3_store or S3BlogStore()
    highlight_key = str(BLOG_STORAGE_CONFIG["WEEKLY_HIGHLIGHT_KEY"])
    highlight = _load_weekly_highlight(store, highlight_key)

    resolved_settings = settings or BlogApiSettings.from_env()
    resolved_repository = repository or build_mongo_repository(resolved_settings)

    slug = str(highlight.get("slug") or "")
    logger.info("Starting weekly digest worker highlight_key=%s slug=%s", highlight_key, slug)

    summary = run_weekly_highlight_email_job(
        repository=resolved_repository,
        highlight=highlight,
    )
    logger.info(
        "Weekly digest worker summary slug=%s attempted=%s sent=%s skipped=%s",
        slug,
        summary["attempted"],
        summary["sent"],
        summary["skipped"],
    )
    return summary


def _load_weekly_highlight(store: S3BlogStore, highlight_key: str) -> dict[str, Any]:
    try:
        raw_highlight = store.read_text(highlight_key)
    except Exception as exc:
        if _is_s3_not_found(exc):
            raise WeeklyDigestJobError(
                f"Weekly highlight object is missing: {highlight_key}"
            ) from exc
        if isinstance(exc, BlogStoreError):
            raise WeeklyDigestJobError(str(exc)) from exc
        raise

    if not raw_highlight.strip():
        raise WeeklyDigestJobError(f"Weekly highlight object is empty: {highlight_key}")

    parsed = json.loads(raw_highlight)
    if not isinstance(parsed, dict):
        raise WeeklyDigestJobError("Weekly highlight artifact must be a JSON object.")
    return dict(parsed)


def main() -> int:
    """Run the one-shot weekly digest worker from a local shell or container command."""
    logging.basicConfig(level=SERVER_CONFIG["LOG_LEVEL"])
    try:
        summary = run_weekly_digest_job()
    except WeeklyDigestJobError as exc:
        logger.error("Weekly digest worker failed: %s", exc)
        return 1
    logger.info("Weekly digest worker summary: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
