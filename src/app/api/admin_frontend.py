"""Site-admin API consumed by the API-client frontend."""

import secrets

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import func, select

from app.api.deps import AuthUser, DbSession
from app.api.actions_runners import _effective_status
from app.models.actions import Runner, WorkflowRun
from app.models.import_job import ImportJob
from app.models.issue import Issue
from app.models.organization import Organization
from app.models.pull_request import PullRequest
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
    # Derived, not the stored column: a runner that stopped heartbeating is
    # offline whichever endpoint is asked. Serving value.status here is what
    # made this page report dozens of phantoms as online while the Actions
    # runner API, fixed first, reported them correctly — the same field
    # serialized from two places disagreeing with itself.
    status = _effective_status(value)
    return {"id": value.id, "name": value.name, "os": value.os, "status": status, "busy": value.busy and status == "online", "labels": value.labels or [], "scope": scope, "last_heartbeat": _fmt_dt(value.last_heartbeat)}


def _import(value: ImportJob) -> dict:
    owner = value.org_login if value.owner_type == "Organization" and value.org_login else value.owner.login
    return {"id": value.id, "job_type": value.job_type, "status": value.status, "source_url": value.source_url, "repo_name": value.repo_name, "owner": owner, "error_message": value.error_message, "repo_count": value.repo_count, "completed_count": value.completed_count, "created_at": _fmt_dt(value.created_at), "completed_at": _fmt_dt(value.completed_at)}


async def _resolve_import_destination(db: DbSession, user: User, body: dict) -> tuple[int, str, str | None]:
    """Resolve the (owner_id, owner_type, org_login) an import should target.

    ``owner_id`` is always a ``users.id`` (the acting admin, unless the
    destination is a User account, in which case it's that account) — see
    ``repo_service.create_repo`` for the same convention. Auto-creates a new
    User or Organization when ``owner_login`` names one that doesn't exist
    yet and ``create_as`` says which kind to create.
    """
    owner_login = str(body.get("owner_login") or "").strip()
    if not owner_login:
        return user.id, "User", None

    existing_user = (await db.execute(select(User).where(User.login == owner_login))).scalar_one_or_none()
    if existing_user is not None:
        return existing_user.id, "User", None

    existing_org = (await db.execute(select(Organization).where(Organization.login == owner_login))).scalar_one_or_none()
    if existing_org is not None:
        return user.id, "Organization", existing_org.login

    create_as = str(body.get("create_as") or "").strip()
    if create_as == "User":
        new_user = await create_user(db, owner_login, secrets.token_urlsafe(24))
        return new_user.id, "User", None
    if create_as == "Organization":
        new_org = Organization(login=owner_login)
        db.add(new_org); await db.commit(); await db.refresh(new_org)
        return user.id, "Organization", new_org.login

    raise HTTPException(status_code=422, detail=f"Unknown user or organization '{owner_login}'")


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
    if not source: raise HTTPException(status_code=422, detail="source_url is required")
    owner_id, owner_type, org_login = await _resolve_import_destination(db, user, body)
    value = await start_single_import(db, source, owner_id, body.get("token"), owner_type, org_login)
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
    values = (
        await db.execute(
            select(
                Issue.id,
                Repository.full_name.label("repository"),
                Issue.number,
                Issue.title,
                Issue.state,
                PullRequest.id.is_not(None).label("is_pull_request"),
                Issue.created_at,
            )
            .join(Repository, Repository.id == Issue.repo_id)
            .outerjoin(PullRequest, PullRequest.issue_id == Issue.id)
            .order_by(Issue.created_at.desc())
            .limit(200)
        )
    ).all()
    return [
        {
            "id": value.id,
            "repository": value.repository,
            "number": value.number,
            "title": value.title,
            "state": value.state,
            "is_pull_request": value.is_pull_request,
            "created_at": _fmt_dt(value.created_at),
        }
        for value in values
    ]
