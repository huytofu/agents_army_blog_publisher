"""Comment routes for generated blog posts."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from blog_manager.api.models import BlogComment, BlogUser
from blog_manager.api.moderation import determine_comment_status
from blog_manager.api.routers.dependencies import get_current_user, get_repository, require_admin

router = APIRouter(prefix="/blog", tags=["blog-comments"])


class CommentCreateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    parent_id: str | None = Field(default=None, max_length=64)


@router.get("/posts/{post_slug}/comments")
def list_comments(post_slug: str, request: Request) -> dict[str, list[dict[str, object]]]:
    comments = get_repository(request).list_approved_comments(post_slug)
    return {"comments": [_public_comment(comment) for comment in comments]}


@router.post("/posts/{post_slug}/comments", status_code=status.HTTP_201_CREATED)
async def create_comment(
    post_slug: str,
    payload: CommentCreateRequest,
    request: Request,
    user: BlogUser = Depends(get_current_user),
) -> dict[str, object]:
    if not user.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email verification required.")
    repository = get_repository(request)
    parent_id = _validate_parent_comment(
        repository=repository,
        post_slug=post_slug,
        parent_id=payload.parent_id,
    )
    decision = await determine_comment_status(user, payload.body)
    comment = repository.create_comment(
        post_slug=post_slug,
        author=user,
        body=payload.body,
        status=decision.status,
        moderation_reason=decision.reason,
        parent_id=parent_id,
    )
    return _public_comment(comment, include_status=True)


@router.post("/admin/comments/{comment_id}/approve")
def approve_comment(
    comment_id: str,
    request: Request,
    user: BlogUser = Depends(get_current_user),
) -> dict[str, object]:
    require_admin(user)
    comment = get_repository(request).update_comment_status(
        comment_id=comment_id,
        status="approved",
        reason="admin_approved",
    )
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found.")
    return _public_comment(comment, include_status=True)


@router.post("/admin/comments/{comment_id}/reject")
def reject_comment(
    comment_id: str,
    request: Request,
    user: BlogUser = Depends(get_current_user),
) -> dict[str, object]:
    require_admin(user)
    comment = get_repository(request).update_comment_status(
        comment_id=comment_id,
        status="rejected",
        reason="admin_rejected",
    )
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found.")
    return _public_comment(comment, include_status=True)


def _validate_parent_comment(
    *,
    repository,
    post_slug: str,
    parent_id: str | None,
) -> str | None:
    if parent_id is None:
        return None
    parent = repository.find_comment_by_id(parent_id)
    if parent is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Parent comment not found.")
    if parent.post_slug != post_slug.strip().casefold():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Parent comment belongs to a different post.",
        )
    if parent.parent_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Replies to replies are not supported.",
        )
    return parent.id


def _public_comment(comment: BlogComment, *, include_status: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": comment.id,
        "post_slug": comment.post_slug,
        "author_username": comment.author_username,
        "body": comment.body,
        "created_at": comment.created_at.isoformat(),
        "parent_id": comment.parent_id,
    }
    if include_status:
        payload["status"] = comment.status
        payload["moderation_reason"] = comment.moderation_reason
    return payload
