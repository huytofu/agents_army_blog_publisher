"""Dormant optional FastAPI entrypoint.

The production blog publisher now runs as an EventBridge-scheduled Lambda
container through `blog_manager.workers.lambda_handler`. This module is kept
only as a marker for a possible future HTTP/dev-debug surface and intentionally
does not import FastAPI, so Lambda packaging does not require web framework
dependencies.
"""

from __future__ import annotations

from typing import NoReturn


app = None


def create_app() -> NoReturn:
    """Fail clearly if someone tries to use the dormant HTTP entrypoint."""
    raise RuntimeError(
        "FastAPI app is dormant. Use blog_manager.workers.lambda_handler.handler "
        "for Lambda or blog_manager.workers.run_blog_job for CLI execution."
    )


__all__ = ["app", "create_app"]
