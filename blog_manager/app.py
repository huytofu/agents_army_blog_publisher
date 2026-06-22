"""FastAPI application entrypoint for the blog publisher service."""

from __future__ import annotations

from fastapi import FastAPI

from blog_manager import __version__
from blog_manager.config import SERVER_CONFIG


def create_app() -> FastAPI:
    app = FastAPI(
        title="Entourage Blog Publisher",
        version=__version__,
        description="Publishes generated Entourage blog posts from S3 idea files.",
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {
            "service": "blog_publisher",
            "status": "ok",
            "version": __version__,
        }

    return app


app = create_app()

__all__ = ["app", "create_app", "SERVER_CONFIG"]
