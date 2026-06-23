"""AWS Lambda entrypoint for EventBridge-scheduled blog publishing runs."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from blog_manager.config import SERVER_CONFIG
from blog_manager.workers.run_blog_job import run_blog_job

logger = logging.getLogger(__name__)


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    """Run the scheduled blog publisher job and return a structured summary."""
    logging.basicConfig(level=SERVER_CONFIG["LOG_LEVEL"])
    payload = event or {}
    request_id = str(getattr(context, "aws_request_id", "") or "")

    try:
        summary = asyncio.run(
            run_blog_job(
                max_ideas=_optional_int(payload.get("max_ideas")),
                dry_run=_optional_bool(payload.get("dry_run")),
            )
        )
        return {
            "ok": summary.ok,
            "request_id": request_id,
            "summary": summary.to_dict(),
        }
    except Exception as exc:
        logger.exception("Lambda blog publisher run failed.")
        return {
            "ok": False,
            "request_id": request_id,
            "error": str(exc),
        }


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
