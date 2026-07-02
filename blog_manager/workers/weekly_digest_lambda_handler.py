"""AWS Lambda entrypoint for EventBridge-scheduled weekly digest runs."""

from __future__ import annotations

import logging
from typing import Any

from blog_manager.config import SERVER_CONFIG
from blog_manager.workers.run_weekly_digest_job import WeeklyDigestJobError, run_weekly_digest_job

logger = logging.getLogger(__name__)


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    """Run the scheduled weekly digest job and return a structured summary."""
    logging.basicConfig(level=SERVER_CONFIG["LOG_LEVEL"])
    request_id = str(getattr(context, "aws_request_id", "") or "")

    try:
        summary = run_weekly_digest_job()
        return {
            "ok": True,
            "request_id": request_id,
            "summary": summary,
        }
    except WeeklyDigestJobError as exc:
        logger.exception("Lambda weekly digest run failed.")
        return {
            "ok": False,
            "request_id": request_id,
            "error": str(exc),
        }
    except Exception as exc:
        logger.exception("Lambda weekly digest run failed.")
        return {
            "ok": False,
            "request_id": request_id,
            "error": str(exc),
        }
