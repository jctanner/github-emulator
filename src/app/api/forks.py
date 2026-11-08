"""Fork endpoints -- create and list forks."""

import asyncio
import os
import shutil

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404, require_installation_grant
from app.models.organization import Organization, OrgMembership
from app.config import settings
from app.models.repository import Repository
from app.api.repos import _repo_json
from app.services.branch_service import record_disk_branches

router = APIRouter(tags=["forks"])

BASE = settings.BASE_URL


@router.post("/repos/{owner}/{repo}/forks", status_code=202)
async def create_fork(
    owner: str, repo: str, body: dict, request: Request, user: AuthUser, db: DbSession,
):
    """Create a fork, of the caller's own or in an organization.

    A fork into ``organization`` is owned by that organization, which the
    caller must be able to create repositories in: an active member, or an
    App installed on it with ``administration: write``. Asking again for a
    fork that already exists returns it, as GitHub does.
    """
    parent = await get_repo_or_404(owner, repo, db)

    target_org = body.get("organization")
    organization = None
    if target_org:
        organization = (
            await db.execute(select(Organization).where(Organization.login == target_org))
        ).scalar_one_or_none()
        if organization is None:
            raise HTTPException(status_code=422, detail="Validation Failed: organization is invalid")
        target_org = organization.login
        if not require_installation_grant(request, organization.login, "administration", write=True):
            membership = (await db.execute(select(OrgMembership).where(
                OrgMembership.org_id == organization.id,
                OrgMembership.user_id == user.id,
                OrgMembership.state == "active",
            ))).scalar_one_or_none()
            if membership is None and not user.site_admin:
                raise HTTPException(status_code=403, detail="Must be an organization member")
    fork_owner_login = target_org or user.login
    fork_name = body.get("name", parent.name)
    full_name = f"{fork_owner_login}/{fork_name}"

    # Check if fork already exists
    existing = (await db.execute(
        select(Repository).where(Repository.full_name == full_name)
    )).scalar_one_or_none()
    if existing is not None:
        if existing.fork and existing.parent_id == parent.id:
            return JSONResponse(status_code=202, content=_repo_json(existing, BASE))
        raise HTTPException(status_code=422, detail="Repository already exists")

    disk_path = os.path.join(settings.DATA_DIR, "repos", fork_owner_login, f"{fork_name}.git")

    fork = Repository(
        owner_id=user.id,
        owner_type="Organization" if organization is not None else "User",
        organization_id=organization.id if organization is not None else None,
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
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail="Unable to clone fork repository")
        await record_disk_branches(db, fork)
        await db.commit()

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
