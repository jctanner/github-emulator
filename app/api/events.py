"""Event endpoints -- public events, repo events, user events."""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.event import Event
from app.models.user import User
from app.schemas.user import SimpleUser, _fmt_dt

router = APIRouter(tags=["events"])

BASE = settings.BASE_URL


def _event_json(event: Event, base_url: str) -> dict:
    actor = SimpleUser.from_db(event.actor, base_url).model_dump() if event.actor else None
    return {
        "id": str(event.id),
        "type": event.type,
        "actor": actor,
        "repo": {
            "id": event.repo_id,
            "name": "",
            "url": "",
        },
        "payload": event.payload or {},
        "public": event.public,
        "created_at": _fmt_dt(event.created_at),
    }


@router.get("/events")
async def list_public_events(
    db: DbSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List public events."""
    query = (
        select(Event)
        .where(Event.public == True)
        .order_by(Event.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    events = (await db.execute(query)).scalars().all()
    return [_event_json(e, BASE) for e in events]


@router.get("/repos/{owner}/{repo}/events")
async def list_repo_events(
    owner: str, repo: str, db: DbSession, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List events for a repository."""
    repository = await get_repo_or_404(owner, repo, db)
    query = (
        select(Event)
        .where(Event.repo_id == repository.id)
        .order_by(Event.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    events = (await db.execute(query)).scalars().all()
    return [_event_json(e, BASE) for e in events]


@router.get("/users/{username}/events")
async def list_user_events(
    username: str, db: DbSession, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List events performed by a user."""
    result = await db.execute(select(User).where(User.login == username))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Not Found")

    query = (
        select(Event)
        .where(Event.actor_id == user.id)
        .order_by(Event.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    events = (await db.execute(query)).scalars().all()
    return [_event_json(e, BASE) for e in events]


@router.get("/users/{username}/received_events")
async def list_user_received_events(
    username: str, db: DbSession, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List events received by a user (stub)."""
    return []
