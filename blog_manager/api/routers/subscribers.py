"""Email subscriber routes for weekly blog highlights."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr

from blog_manager.api.routers.dependencies import get_repository

router = APIRouter(prefix="/blog/subscribers", tags=["blog-subscribers"])


class SubscribeRequest(BaseModel):
    email: EmailStr


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def subscribe(payload: SubscribeRequest, request: Request) -> dict[str, str]:
    repository = get_repository(request)
    subscriber = repository.upsert_subscriber(email=str(payload.email))
    repository.create_email_token(email=subscriber.email, purpose="confirm_subscription")
    return {"status": "pending_confirmation"}


@router.get("/confirm")
def confirm_subscription(token: str, request: Request) -> dict[str, str]:
    repository = get_repository(request)
    email_token = repository.consume_email_token(token=token, purpose="confirm_subscription")
    if email_token is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid confirmation token.")
    subscriber = repository.update_subscriber_status(email=email_token.email, status="confirmed")
    if subscriber is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscriber not found.")
    repository.create_email_token(email=email_token.email, purpose="unsubscribe")
    return {"status": "confirmed"}


@router.get("/unsubscribe")
def unsubscribe(token: str, request: Request) -> dict[str, str]:
    repository = get_repository(request)
    email_token = repository.consume_email_token(token=token, purpose="unsubscribe")
    if email_token is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid unsubscribe token.")
    subscriber = repository.update_subscriber_status(email=email_token.email, status="unsubscribed")
    if subscriber is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscriber not found.")
    return {"status": "unsubscribed"}
