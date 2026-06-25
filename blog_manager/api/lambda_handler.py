"""AWS Lambda entrypoint for the public blog FastAPI service."""

from __future__ import annotations

from mangum import Mangum

from blog_manager.api.app import get_app

handler = Mangum(get_app())
