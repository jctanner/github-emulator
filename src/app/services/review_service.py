"""Pull request review service."""

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Review


async def create_review(
    db: AsyncSession,
    pr_id: int,
    user_id: int,
    body: Optional[str] = None,
    state: str = "PENDING",
    commit_id: Optional[str] = None,
) -> Review:
    """Create a new pull request review.

    Args:
        db: Async database session.
        pr_id: The pull request ID.
        user_id: The ID of the reviewing user.
        body: Review body text.
        state: Review state ("PENDING", "APPROVED", "CHANGES_REQUESTED", "COMMENTED").
        commit_id: The commit SHA being reviewed.

    Returns:
        The newly created Review.
    """
    review = Review(
        pull_request_id=pr_id,
        user_id=user_id,
        body=body,
        state=state,
        commit_id=commit_id or "",
    )

    # If the review is not pending, set submitted_at
    if state != "PENDING":
        review.submitted_at = datetime.utcnow()

    db.add(review)
    await db.commit()
    await db.refresh(review)
    return review


async def list_reviews(
    db: AsyncSession, pr_id: int
) -> list[Review]:
    """List all reviews for a pull request.

    Args:
        db: Async database session.
        pr_id: The pull request ID.

    Returns:
        List of Reviews ordered by creation time.
    """
    result = await db.execute(
        select(Review)
        .where(Review.pull_request_id == pr_id)
        .order_by(Review.created_at.asc())
    )
    return list(result.scalars().all())


async def submit_review(
    db: AsyncSession,
    review: Review,
    state: str,
    body: Optional[str] = None,
) -> Review:
    """Submit a pending review.

    Transitions a review from PENDING to a final state.

    Args:
        db: Async database session.
        review: The review to submit.
        state: The final state ("APPROVED", "CHANGES_REQUESTED", "COMMENTED").
        body: Optional review body to set/update.

    Returns:
        The updated Review.
    """
    review.state = state
    review.submitted_at = datetime.utcnow()

    if body is not None:
        review.body = body

    await db.commit()
    await db.refresh(review)
    return review
