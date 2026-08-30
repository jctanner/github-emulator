"""Deploy key endpoints -- CRUD under /repos/{owner}/{repo}/keys."""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.deploy_key import DeployKey
from app.schemas.user import _fmt_dt

router = APIRouter(tags=["deploy-keys"])

BASE = settings.BASE_URL


def _key_json(key: DeployKey, owner: str, repo_name: str, base_url: str) -> dict:
    api = f"{base_url}/api/v3"
    return {
        "id": key.id,
        "key": key.key,
        "url": f"{api}/repos/{owner}/{repo_name}/keys/{key.id}",
        "title": key.title,
        "verified": key.verified,
        "created_at": _fmt_dt(key.created_at),
        "read_only": key.read_only,
    }


@router.get("/repos/{owner}/{repo}/keys")
async def list_deploy_keys(
    owner: str, repo: str, db: DbSession, user: AuthUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List deploy keys."""
    repository = await get_repo_or_404(owner, repo, db)
    query = (
        select(DeployKey)
        .where(DeployKey.repo_id == repository.id)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    keys = (await db.execute(query)).scalars().all()
    return [_key_json(k, owner, repo, BASE) for k in keys]


@router.post("/repos/{owner}/{repo}/keys", status_code=201)
async def create_deploy_key(
    owner: str, repo: str, body: dict, user: AuthUser, db: DbSession,
):
    """Add a deploy key."""
    repository = await get_repo_or_404(owner, repo, db)

    title = body.get("title", "")
    key_value = body.get("key", "")
    read_only = body.get("read_only", True)

    if not key_value:
        raise HTTPException(status_code=422, detail="key is required")

    dk = DeployKey(
        repo_id=repository.id,
        title=title,
        key=key_value,
        read_only=read_only,
    )
    db.add(dk)
    await db.commit()
    await db.refresh(dk)
    return _key_json(dk, owner, repo, BASE)


@router.get("/repos/{owner}/{repo}/keys/{key_id}")
async def get_deploy_key(
    owner: str, repo: str, key_id: int, db: DbSession, user: AuthUser,
):
    """Get a deploy key."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(DeployKey).where(DeployKey.id == key_id, DeployKey.repo_id == repository.id)
    )
    dk = result.scalar_one_or_none()
    if dk is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _key_json(dk, owner, repo, BASE)


@router.delete("/repos/{owner}/{repo}/keys/{key_id}", status_code=204)
async def delete_deploy_key(
    owner: str, repo: str, key_id: int, user: AuthUser, db: DbSession,
):
    """Delete a deploy key."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(DeployKey).where(DeployKey.id == key_id, DeployKey.repo_id == repository.id)
    )
    dk = result.scalar_one_or_none()
    if dk is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await db.delete(dk)
    await db.commit()
