"""Notification endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import select

from app.api.deps import AuthUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.notification import Notification
from app.schemas.user import _fmt_dt

router = APIRouter(tags=["notifications"])

BASE = settings.BASE_URL


def _notification_json(n: Notification, base_url: str) -> dict:
    return {
        "id": str(n.id),
        "repository": {
            "id": n.repo_id,
            "full_name": "",
        },
        "subject": {
            "title": n.subject_title,
            "url": n.subject_url,
            "latest_comment_url": None,
            "type": n.subject_type,
        },
        "reason": n.reason,
        "unread": n.unread,
        "updated_at": _fmt_dt(n.updated_at),
        "last_read_at": _fmt_dt(n.last_read_at),
        "url": f"{base_url}/api/v3/notifications/threads/{n.id}",
    }


@router.get("/notifications")
async def list_notifications(
    user: AuthUser, db: DbSession,
    all: bool = Query(False),
    participating: bool = Query(False),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List notifications for the authenticated user."""
    query = select(Notification).where(Notification.user_id == user.id)
    if not all:
        query = query.where(Notification.unread == True)

    query = (
        query.order_by(Notification.updated_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    notifications = (await db.execute(query)).scalars().all()
    return [_notification_json(n, BASE) for n in notifications]


@router.put("/notifications", status_code=205)
async def mark_notifications_read(user: AuthUser, db: DbSession, body: dict = {}):
    """Mark notifications as read."""
    from sqlalchemy import update

    now = datetime.now(timezone.utc)
    await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.unread == True)
        .values(unread=False, last_read_at=now)
    )
    await db.commit()
    return Response(status_code=205)


@router.get("/repos/{owner}/{repo}/notifications")
async def list_repo_notifications(
    owner: str, repo: str, user: AuthUser, db: DbSession,
    all: bool = Query(False),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List notifications for a repository."""
    repository = await get_repo_or_404(owner, repo, db)

    query = select(Notification).where(
        Notification.user_id == user.id,
        Notification.repo_id == repository.id,
    )
    if not all:
        query = query.where(Notification.unread == True)

    query = (
        query.order_by(Notification.updated_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    notifications = (await db.execute(query)).scalars().all()
    return [_notification_json(n, BASE) for n in notifications]


@router.get("/notifications/threads/{thread_id}")
async def get_notification_thread(thread_id: int, user: AuthUser, db: DbSession):
    """Get a notification thread."""
    result = await db.execute(
        select(Notification).where(
            Notification.id == thread_id, Notification.user_id == user.id
        )
    )
    n = result.scalar_one_or_none()
    if n is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _notification_json(n, BASE)


@router.patch("/notifications/threads/{thread_id}", status_code=205)
async def mark_thread_read(thread_id: int, user: AuthUser, db: DbSession):
    """Mark a notification thread as read."""
    result = await db.execute(
        select(Notification).where(
            Notification.id == thread_id, Notification.user_id == user.id
        )
    )
    n = result.scalar_one_or_none()
    if n is None:
        raise HTTPException(status_code=404, detail="Not Found")

    n.unread = False
    n.last_read_at = datetime.now(timezone.utc)
    await db.commit()
    return Response(status_code=205)
