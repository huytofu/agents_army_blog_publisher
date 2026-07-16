"""FastAPI dependencies shared by blog API routers."""

from __future__ import annotations

from fastapi import Header, HTTPException, Request, status

from blog_manager.api.config import BlogApiSettings
from blog_manager.api.models import BlogUser
from blog_manager.api.repositories import BlogRepository
from blog_manager.api.security import BlogAuthError, decode_access_token


def get_settings(request: Request) -> BlogApiSettings:
    return request.app.state.blog_api_settings


def get_repository(request: Request) -> BlogRepository:
    return request.app.state.blog_repository


def get_current_user(
    request: Request,
    authorization: str = Header(default=""),
) -> BlogUser:
    settings = get_settings(request)
    repository = get_repository(request)
    scheme, _, token = authorization.partition(" ")
    if scheme.casefold() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    try:
        user_id = decode_access_token(token, settings=settings)
    except BlogAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    user = repository.find_user_by_id(user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    return user


def require_admin(user: BlogUser) -> BlogUser:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return user
