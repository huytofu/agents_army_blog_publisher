"""Blog-only username/password auth routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from blog_manager.api.models import BlogUser
from blog_manager.api.routers.dependencies import get_current_user, get_repository, get_settings
from blog_manager.api.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/blog/auth", tags=["blog-auth"])


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_]+$")
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request) -> dict[str, str]:
    repository = get_repository(request)
    if repository.find_user_by_username(payload.username) or repository.find_user_by_email(str(payload.email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Blog user already exists.")
    user = repository.create_user(
        username=payload.username,
        email=str(payload.email),
        password_hash=hash_password(payload.password),
    )
    repository.create_email_token(email=user.email, purpose="verify_email")
    return {"status": "verification_required"}


@router.get("/verify-email")
def verify_email(token: str, request: Request) -> dict[str, str]:
    repository = get_repository(request)
    email_token = repository.consume_email_token(token=token, purpose="verify_email")
    if email_token is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification token.")
    if repository.mark_user_email_verified(email_token.email) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blog user not found.")
    return {"status": "verified"}


@router.post("/login")
def login(payload: LoginRequest, request: Request) -> dict[str, str]:
    repository = get_repository(request)
    settings = get_settings(request)
    user = repository.find_user_by_username(payload.username)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")
    return {
        "access_token": create_access_token(user_id=user.id, settings=settings),
        "token_type": "bearer",
    }


@router.get("/me")
def me(user: BlogUser = Depends(get_current_user)) -> dict[str, object]:
    return _public_user(user)


def _public_user(user: BlogUser) -> dict[str, object]:
    return {
        "username": user.username,
        "email": user.email,
        "email_verified": user.email_verified,
        "role": user.role,
    }
