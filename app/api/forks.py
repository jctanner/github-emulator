"""Fork endpoints -- create and list forks."""

import asyncio
import os
import shutil

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.repository import Repository
from app.api.repos import _repo_json

router = APIRouter(tags=["forks"])

BASE = settings.BASE_URL


@router.post("/repos/{owner}/{repo}/forks", status_code=202)
async def create_fork(
    owner: str, repo: str, body: dict, user: AuthUser, db: DbSession,
):
    """Create a fork."""
    parent = await get_repo_or_404(owner, repo, db)

    target_org = body.get("organization")
    fork_owner_login = target_org or user.login
    fork_name = body.get("name", parent.name)
    full_name = f"{fork_owner_login}/{fork_name}"

    # Check if fork already exists
    existing = await db.execute(
        select(Repository).where(Repository.full_name == full_name)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=422, detail="Repository already exists")

    disk_path = os.path.join(settings.DATA_DIR, "repos", fork_owner_login, f"{fork_name}.git")

    fork = Repository(
        owner_id=user.id,
        owner_type="User",
        name=fork_name,
        full_name=full_name,
        description=parent.description,
        private=parent.private,
        fork=True,
        parent_id=parent.id,
        default_branch=parent.default_branch,
        disk_path=disk_path,
        visibility=parent.visibility,
    )
    db.add(fork)
    parent.forks_count += 1
    await db.commit()
    await db.refresh(fork)

    # Clone the bare repo on disk
    if parent.disk_path and os.path.isdir(parent.disk_path):
        os.makedirs(os.path.dirname(disk_path), exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--bare", parent.disk_path, disk_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    return _repo_json(fork, BASE)


@router.get("/repos/{owner}/{repo}/forks")
async def list_forks(
    owner: str, repo: str, db: DbSession, current_user: CurrentUser,
    sort: str = Query("newest"),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List forks of a repository."""
    repository = await get_repo_or_404(owner, repo, db)

    query = (
        select(Repository)
        .where(Repository.parent_id == repository.id)
    )

    if sort == "oldest":
        query = query.order_by(Repository.created_at.asc())
    elif sort == "stargazers":
        query = query.order_by(Repository.stargazers_count.desc())
    elif sort == "watchers":
        query = query.order_by(Repository.watchers_count.desc())
    else:
        query = query.order_by(Repository.created_at.desc())

    query = query.offset((page - 1) * per_page).limit(per_page)
    forks = (await db.execute(query)).scalars().all()

    return [_repo_json(f, BASE) for f in forks]
