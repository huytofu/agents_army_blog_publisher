"""FastAPI app factory for the public blog reader API."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from blog_manager.api.config import BlogApiSettings
from blog_manager.api.database import build_mongo_repository
from blog_manager.api.repositories import BlogRepository
from blog_manager.api.routers import auth, comments, subscribers


def create_app(
    *,
    settings: BlogApiSettings | None = None,
    repository: BlogRepository | None = None,
) -> FastAPI:
    resolved_settings = settings or BlogApiSettings.from_env()
    resolved_repository = repository or build_mongo_repository(resolved_settings)
    app = FastAPI(title="ENTOURAGE Blog API", version="1.0.0")
    app.state.blog_api_settings = resolved_settings
    app.state.blog_repository = resolved_repository

    if resolved_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved_settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.get("/blog/health", tags=["blog-health"])
    def health() -> dict[str, object]:
        return {"ok": True, "service": "blog-api"}

    app.include_router(auth.router)
    app.include_router(comments.router)
    app.include_router(subscribers.router)
    return app


app: FastAPI | None = None


def get_app() -> FastAPI:
    global app
    if app is None:
        app = create_app()
    return app
