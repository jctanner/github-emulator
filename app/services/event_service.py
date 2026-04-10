"""Event recording and listing service."""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event


async def record_event(
    db: AsyncSession,
    event_type: str,
    actor_id: int,
    repo_id: Optional[int] = None,
    org_id: Optional[int] = None,
    payload: Optional[dict] = None,
    public: bool = True,
) -> Event:
    """Record a new event.

    Args:
        db: Async database session.
        event_type: The event type (e.g. "PushEvent", "CreateEvent").
        actor_id: The ID of the user who triggered the event.
        repo_id: Optional repository ID.
        org_id: Optional organization ID.
        payload: The event payload dict.
        public: Whether the event is public.

    Returns:
        The newly created Event.
    """
    if payload is None:
        payload = {}

    event = Event(
        type=event_type,
        actor_id=actor_id,
        repo_id=repo_id,
        org_id=org_id,
        payload=payload,
        public=public,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


async def list_events(
    db: AsyncSession,
    page: int = 1,
    per_page: int = 30,
) -> list[Event]:
    """List all public events with pagination.

    Args:
        db: Async database session.
        page: Page number (1-indexed).
        per_page: Number of results per page.

    Returns:
        List of Events ordered by most recent first.
    """
    offset = (page - 1) * per_page
    result = await db.execute(
        select(Event)
        .where(Event.public == True)  # noqa: E712
        .order_by(Event.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    return list(result.scalars().all())


async def list_repo_events(
    db: AsyncSession,
    repo_id: int,
    page: int = 1,
    per_page: int = 30,
) -> list[Event]:
    """List events for a specific repository.

    Args:
        db: Async database session.
        repo_id: The repository ID.
        page: Page number (1-indexed).
        per_page: Number of results per page.

    Returns:
        List of Events for the repo ordered by most recent first.
    """
    offset = (page - 1) * per_page
    result = await db.execute(
        select(Event)
        .where(Event.repo_id == repo_id)
        .order_by(Event.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    return list(result.scalars().all())
