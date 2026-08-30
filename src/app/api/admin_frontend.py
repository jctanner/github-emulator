"""Site-admin API consumed by the API-client frontend."""

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import func, select

from app.api.deps import AuthUser, DbSession
from app.models.actions import Runner, WorkflowRun
from app.models.import_job import ImportJob
from app.models.issue import Issue
from app.models.organization import Organization
from app.models.repository import Repository
from app.models.token import PersonalAccessToken
from app.models.user import User
from app.schemas.admin import (
    AdminImportResponse, AdminIssueResponse, AdminOrganizationResponse, AdminRepositoryResponse,
    AdminRunnerResponse, AdminSummaryResponse, AdminTokenCreatedResponse,
    AdminTokenResponse, AdminUserResponse,
)
from app.schemas.user import _fmt_dt
from app.services.auth_service import hash_password
from app.services.repo_service import delete_repo
from app.services.user_service import create_token, create_user
from app.services.import_service import start_single_import

router = APIRouter(prefix="/admin/api", tags=["admin-frontend"])


def _require_admin(user) -> None:
    if not user.site_admin:
        raise HTTPException(status_code=403, detail="site admin required")


def _user(value: User) -> dict:
    return {"id": value.id, "login": value.login, "name": value.name, "email": value.email, "site_admin": value.site_admin, "type": value.type, "created_at": _fmt_dt(value.created_at)}


def _org(value: Organization) -> dict:
    return {"id": value.id, "login": value.login, "name": value.name, "description": value.description, "email": value.email, "created_at": _fmt_dt(value.created_at)}


def _repo(value: Repository) -> dict:
    return {"id": value.id, "full_name": value.full_name, "name": value.name, "private": value.private, "owner_type": value.owner_type, "default_branch": value.default_branch, "created_at": _fmt_dt(value.created_at)}


def _token(value: PersonalAccessToken) -> dict:
    return {"id": value.id, "user_id": value.user_id, "owner": value.user.login, "name": value.name, "token_prefix": value.token_prefix, "scopes": value.scopes or [], "created_at": _fmt_dt(value.created_at), "last_used_at": _fmt_dt(value.last_used_at)}


def _runner(value: Runner) -> dict:
    scope = value.enterprise_slug or (f"repository:{value.repo_id}" if value.repo_id else f"organization:{value.org_id}" if value.org_id else "site")
    return {"id": value.id, "name": value.name, "os": value.os, "status": value.status, "busy": value.busy, "labels": value.labels or [], "scope": scope, "last_heartbeat": _fmt_dt(value.last_heartbeat)}


def _import(value: ImportJob) -> dict:
    return {"id": value.id, "job_type": value.job_type, "status": value.status, "source_url": value.source_url, "repo_name": value.repo_name, "owner": value.owner.login, "error_message": value.error_message, "repo_count": value.repo_count, "completed_count": value.completed_count, "created_at": _fmt_dt(value.created_at), "completed_at": _fmt_dt(value.completed_at)}


@router.get("/summary", response_model=AdminSummaryResponse)
async def summary(user: AuthUser, db: DbSession):
    _require_admin(user)
    async def count(model):
        return (await db.execute(select(func.count()).select_from(model))).scalar() or 0
    return {"users": await count(User), "organizations": await count(Organization), "repositories": await count(Repository), "issues": await count(Issue), "workflow_runs": await count(WorkflowRun), "runners": await count(Runner), "imports": await count(ImportJob)}


@router.get("/users", response_model=list[AdminUserResponse])
async def users(user: AuthUser, db: DbSession):
    _require_admin(user)
    return [_user(item) for item in (await db.execute(select(User).order_by(User.id))).scalars().all()]


@router.post("/users", response_model=AdminUserResponse, status_code=201)
async def add_user(body: dict, user: AuthUser, db: DbSession):
    _require_admin(user)
    if not body.get("login") or not body.get("password"):
        raise HTTPException(status_code=422, detail="login and password are required")
    if (await db.execute(select(User).where(User.login == body["login"]))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="login already exists")
    return _user(await create_user(db, body["login"], body["password"], body.get("name"), body.get("email"), bool(body.get("site_admin"))))


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
async def edit_user(user_id: int, body: dict, user: AuthUser, db: DbSession):
    _require_admin(user)
    value = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if value is None: raise HTTPException(status_code=404, detail="Not Found")
    for name in ("name", "email", "site_admin"):
        if name in body: setattr(value, name, body[name])
    if body.get("password"): value.hashed_password = hash_password(body["password"])
    await db.commit(); await db.refresh(value)
    return _user(value)


@router.delete("/users/{user_id}", status_code=204)
async def remove_user(user_id: int, user: AuthUser, db: DbSession):
    _require_admin(user)
    if user_id == user.id: raise HTTPException(status_code=409, detail="cannot delete current user")
    value = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if value is None: raise HTTPException(status_code=404, detail="Not Found")
    await db.delete(value); await db.commit(); return Response(status_code=204)


@router.get("/organizations", response_model=list[AdminOrganizationResponse])
async def organizations(user: AuthUser, db: DbSession):
    _require_admin(user)
    return [_org(item) for item in (await db.execute(select(Organization).order_by(Organization.id))).scalars().all()]


@router.post("/organizations", response_model=AdminOrganizationResponse, status_code=201)
async def add_organization(body: dict, user: AuthUser, db: DbSession):
    _require_admin(user)
    login = str(body.get("login") or "").strip()
    if not login: raise HTTPException(status_code=422, detail="login is required")
    if (await db.execute(select(Organization).where(Organization.login == login))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="login already exists")
    value = Organization(login=login, name=body.get("name"), description=body.get("description"), email=body.get("email"))
    db.add(value); await db.commit(); await db.refresh(value); return _org(value)


@router.patch("/organizations/{org_id}", response_model=AdminOrganizationResponse)
async def edit_org(org_id: int, body: dict, user: AuthUser, db: DbSession):
    _require_admin(user)
    value = (await db.execute(select(Organization).where(Organization.id == org_id))).scalar_one_or_none()
    if value is None: raise HTTPException(status_code=404, detail="Not Found")
    for name in ("name", "description", "email"):
        if name in body: setattr(value, name, body[name])
    await db.commit(); await db.refresh(value); return _org(value)


@router.delete("/organizations/{org_id}", status_code=204)
async def remove_org(org_id: int, user: AuthUser, db: DbSession):
    _require_admin(user)
    value = (await db.execute(select(Organization).where(Organization.id == org_id))).scalar_one_or_none()
    if value is None: raise HTTPException(status_code=404, detail="Not Found")
    await db.delete(value); await db.commit(); return Response(status_code=204)


@router.get("/repositories", response_model=list[AdminRepositoryResponse])
async def repositories(user: AuthUser, db: DbSession):
    _require_admin(user)
    return [_repo(item) for item in (await db.execute(select(Repository).order_by(Repository.id))).scalars().all()]


@router.delete("/repositories/{repo_id}", status_code=204)
async def remove_repo(repo_id: int, user: AuthUser, db: DbSession):
    _require_admin(user)
    value = (await db.execute(select(Repository).where(Repository.id == repo_id))).scalar_one_or_none()
    if value is None: raise HTTPException(status_code=404, detail="Not Found")
    await delete_repo(db, value); return Response(status_code=204)


@router.get("/tokens", response_model=list[AdminTokenResponse])
async def tokens(user: AuthUser, db: DbSession):
    _require_admin(user)
    return [_token(item) for item in (await db.execute(select(PersonalAccessToken).order_by(PersonalAccessToken.id))).scalars().all()]


@router.post("/tokens", response_model=AdminTokenCreatedResponse, status_code=201)
async def add_token(body: dict, user: AuthUser, db: DbSession):
    _require_admin(user)
    token, raw = await create_token(db, int(body["user_id"]), str(body.get("name") or "API token"), list(body.get("scopes") or []))
    return {**_token(token), "token": raw}


@router.delete("/tokens/{token_id}", status_code=204)
async def remove_token(token_id: int, user: AuthUser, db: DbSession):
    _require_admin(user)
    value = (await db.execute(select(PersonalAccessToken).where(PersonalAccessToken.id == token_id))).scalar_one_or_none()
    if value is None: raise HTTPException(status_code=404, detail="Not Found")
    await db.delete(value); await db.commit(); return Response(status_code=204)


@router.get("/runners", response_model=list[AdminRunnerResponse])
async def runners(user: AuthUser, db: DbSession):
    _require_admin(user)
    return [_runner(item) for item in (await db.execute(select(Runner).order_by(Runner.id))).scalars().all()]


@router.get("/imports", response_model=list[AdminImportResponse])
async def imports(user: AuthUser, db: DbSession):
    _require_admin(user)
    return [_import(item) for item in (await db.execute(select(ImportJob).order_by(ImportJob.created_at.desc()))).scalars().all()]


@router.post("/imports", response_model=AdminImportResponse, status_code=201)
async def add_import(body: dict, user: AuthUser, db: DbSession):
    _require_admin(user)
    source = str(body.get("source_url") or "").strip()
    owner_id = int(body.get("owner_id") or user.id)
    if not source: raise HTTPException(status_code=422, detail="source_url is required")
    value = await start_single_import(db, source, owner_id, body.get("token"))
    return _import(value)


@router.get("/imports/{job_id}", response_model=AdminImportResponse)
async def import_job(job_id: int, user: AuthUser, db: DbSession):
    _require_admin(user)
    value = (await db.execute(select(ImportJob).where(ImportJob.id == job_id))).scalar_one_or_none()
    if value is None: raise HTTPException(status_code=404, detail="Not Found")
    return _import(value)


@router.get("/issues", response_model=list[AdminIssueResponse])
async def issues(user: AuthUser, db: DbSession):
    _require_admin(user)
    values = (await db.execute(select(Issue).order_by(Issue.created_at.desc()).limit(200))).scalars().all()
    result = []
    for value in values:
        repository = (await db.execute(select(Repository).where(Repository.id == value.repo_id))).scalar_one()
        result.append({"id": value.id, "repository": repository.full_name, "number": value.number, "title": value.title, "state": value.state, "is_pull_request": value.pull_request is not None, "created_at": _fmt_dt(value.created_at)})
    return result
