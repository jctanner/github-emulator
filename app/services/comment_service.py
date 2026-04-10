"""Issue comment management service."""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IssueComment


async def create_issue_comment(
    db: AsyncSession,
    issue_id: int,
    user_id: int,
    body: str,
) -> IssueComment:
    """Create a new comment on an issue.

    Args:
        db: Async database session.
        issue_id: The ID of the issue to comment on.
        user_id: The ID of the user creating the comment.
        body: The comment body (markdown).

    Returns:
        The newly created IssueComment.
    """
    comment = IssueComment(
        issue_id=issue_id,
        user_id=user_id,
        body=body,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return comment


async def get_issue_comment(
    db: AsyncSession, comment_id: int
) -> Optional[IssueComment]:
    """Get a comment by its ID.

    Args:
        db: Async database session.
        comment_id: The comment ID.

    Returns:
        The IssueComment, or None if not found.
    """
    result = await db.execute(
        select(IssueComment).where(IssueComment.id == comment_id)
    )
    return result.scalar_one_or_none()


async def update_issue_comment(
    db: AsyncSession, comment: IssueComment, body: str
) -> IssueComment:
    """Update a comment's body.

    Args:
        db: Async database session.
        comment: The comment to update.
        body: The new comment body.

    Returns:
        The updated IssueComment.
    """
    comment.body = body
    await db.commit()
    await db.refresh(comment)
    return comment


async def delete_issue_comment(
    db: AsyncSession, comment: IssueComment
) -> None:
    """Delete a comment.

    Args:
        db: Async database session.
        comment: The comment to delete.
    """
    await db.delete(comment)
    await db.commit()


async def list_issue_comments(
    db: AsyncSession,
    issue_id: int,
    page: int = 1,
    per_page: int = 30,
) -> list[IssueComment]:
    """List comments on an issue with pagination.

    Args:
        db: Async database session.
        issue_id: The issue ID.
        page: Page number (1-indexed).
        per_page: Number of results per page.

    Returns:
        List of IssueComments ordered by creation time.
    """
    offset = (page - 1) * per_page
    result = await db.execute(
        select(IssueComment)
        .where(IssueComment.issue_id == issue_id)
        .order_by(IssueComment.created_at.asc())
        .offset(offset)
        .limit(per_page)
    )
    return list(result.scalars().all())
