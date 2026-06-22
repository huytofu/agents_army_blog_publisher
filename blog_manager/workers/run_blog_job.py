"""Cron-friendly entrypoint for future blog publishing jobs."""

from __future__ import annotations

import logging

from blog_manager.config import BLOG_STORAGE_CONFIG, SERVER_CONFIG, WORKER_CONFIG

logger = logging.getLogger(__name__)


def main() -> int:
    """Load configuration and reserve the worker entrypoint for pipeline wiring."""
    logging.basicConfig(level=SERVER_CONFIG["LOG_LEVEL"])
    logger.info(
        "Blog publisher worker configured, dry_run=%s max_ideas=%s bucket_configured=%s",
        WORKER_CONFIG["DRY_RUN"],
        WORKER_CONFIG["MAX_IDEAS_PER_RUN"],
        bool(BLOG_STORAGE_CONFIG["S3_BUCKET"]),
    )
    logger.info("Blog generation graph is not wired yet; scaffold/config phase complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
