"""Webhook endpoints -- CRUD and delivery listing."""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.webhook import Webhook, WebhookDelivery
from app.schemas.user import _fmt_dt, _make_node_id

router = APIRouter(tags=["webhooks"])

BASE = settings.BASE_URL


def _hook_json(hook: Webhook, owner: str, repo_name: str, base_url: str) -> dict:
    api = f"{base_url}/api/v3"
    return {
        "type": "Repository",
        "id": hook.id,
        "name": "web",
        "active": hook.active,
        "events": hook.events or ["push"],
        "config": {
            "content_type": hook.content_type,
            "insecure_ssl": "1" if hook.insecure_ssl else "0",
            "url": hook.url,
        },
        "updated_at": _fmt_dt(hook.updated_at),
        "created_at": _fmt_dt(hook.created_at),
        "url": f"{api}/repos/{owner}/{repo_name}/hooks/{hook.id}",
        "test_url": f"{api}/repos/{owner}/{repo_name}/hooks/{hook.id}/tests",
        "ping_url": f"{api}/repos/{owner}/{repo_name}/hooks/{hook.id}/pings",
        "deliveries_url": f"{api}/repos/{owner}/{repo_name}/hooks/{hook.id}/deliveries",
        "last_response": {"code": None, "status": "unused", "message": None},
    }


@router.get("/repos/{owner}/{repo}/hooks")
async def list_hooks(
    owner: str, repo: str, db: DbSession, user: AuthUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List repository webhooks."""
    repository = await get_repo_or_404(owner, repo, db)
    query = (
        select(Webhook)
        .where(Webhook.repo_id == repository.id)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    hooks = (await db.execute(query)).scalars().all()
    return [_hook_json(h, owner, repo, BASE) for h in hooks]


@router.post("/repos/{owner}/{repo}/hooks", status_code=201)
async def create_hook(
    owner: str, repo: str, body: dict, user: AuthUser, db: DbSession
):
    """Create a webhook."""
    repository = await get_repo_or_404(owner, repo, db)

    config = body.get("config", {})
    url = config.get("url")
    if not url:
        raise HTTPException(status_code=422, detail="config.url is required")

    hook = Webhook(
        repo_id=repository.id,
        url=url,
        secret=config.get("secret"),
        content_type=config.get("content_type", "json"),
        insecure_ssl=config.get("insecure_ssl", "0") == "1",
        events=body.get("events", ["push"]),
        active=body.get("active", True),
    )
    db.add(hook)
    await db.commit()
    await db.refresh(hook)
    return _hook_json(hook, owner, repo, BASE)


@router.get("/repos/{owner}/{repo}/hooks/{hook_id}")
async def get_hook(
    owner: str, repo: str, hook_id: int, db: DbSession, user: AuthUser
):
    """Get a single webhook."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Webhook).where(Webhook.id == hook_id, Webhook.repo_id == repository.id)
    )
    hook = result.scalar_one_or_none()
    if hook is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _hook_json(hook, owner, repo, BASE)


@router.patch("/repos/{owner}/{repo}/hooks/{hook_id}")
async def update_hook(
    owner: str, repo: str, hook_id: int, body: dict, user: AuthUser, db: DbSession
):
    """Update a webhook."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Webhook).where(Webhook.id == hook_id, Webhook.repo_id == repository.id)
    )
    hook = result.scalar_one_or_none()
    if hook is None:
        raise HTTPException(status_code=404, detail="Not Found")

    config = body.get("config", {})
    if "url" in config:
        hook.url = config["url"]
    if "secret" in config:
        hook.secret = config["secret"]
    if "content_type" in config:
        hook.content_type = config["content_type"]
    if "insecure_ssl" in config:
        hook.insecure_ssl = config["insecure_ssl"] == "1"
    if "events" in body:
        hook.events = body["events"]
    if "active" in body:
        hook.active = body["active"]

    await db.commit()
    await db.refresh(hook)
    return _hook_json(hook, owner, repo, BASE)


@router.delete("/repos/{owner}/{repo}/hooks/{hook_id}", status_code=204)
async def delete_hook(
    owner: str, repo: str, hook_id: int, user: AuthUser, db: DbSession
):
    """Delete a webhook."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Webhook).where(Webhook.id == hook_id, Webhook.repo_id == repository.id)
    )
    hook = result.scalar_one_or_none()
    if hook is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await db.delete(hook)
    await db.commit()


@router.get("/repos/{owner}/{repo}/hooks/{hook_id}/deliveries")
async def list_deliveries(
    owner: str, repo: str, hook_id: int, db: DbSession, user: AuthUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List deliveries for a webhook."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Webhook).where(Webhook.id == hook_id, Webhook.repo_id == repository.id)
    )
    hook = result.scalar_one_or_none()
    if hook is None:
        raise HTTPException(status_code=404, detail="Not Found")

    query = (
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == hook.id)
        .order_by(WebhookDelivery.delivered_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    deliveries = (await db.execute(query)).scalars().all()

    return [
        {
            "id": d.id,
            "event": d.event,
            "action": d.action,
            "status_code": d.status_code,
            "delivered_at": _fmt_dt(d.delivered_at),
            "duration": d.duration,
            "success": d.success,
        }
        for d in deliveries
    ]
