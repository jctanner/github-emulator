"""Site-admin API consumed by the API-client frontend."""

import secrets

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import func, select

from app.api.deps import AuthUser, DbSession
from app.api.actions_runners import (
    _delete_runner_and_detach_history, _effective_status,
)
from app.models.actions import Runner, Workflow, WorkflowJob, WorkflowRun
from app.models.import_job import ImportJob
from app.models.issue import Issue
from app.models.organization import Organization
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.token import PersonalAccessToken
from app.models.user import User
from app.schemas.admin import (
    AdminActiveRunResponse,
    AdminImportResponse, AdminIssueResponse, AdminOrganizationResponse, AdminRepositoryResponse,
    AdminRunnerResponse, AdminSummaryResponse, AdminTokenCreatedResponse,
    AdminTokenResponse, AdminUserResponse,
)
from app.schemas.user import _fmt_dt
from app.services.auth_service import hash_password
from app.services.repo_service import delete_repo
from app.services.user_service import create_token, create_user
from app.services.import_service import start_single_import
from app.services.workflow_scheduler import cancel_workflow_run

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


def _runner(
    value: Runner,
    repo_names: dict[int, str] | None = None,
    org_logins: dict[int, str] | None = None,
) -> dict:
    # The scope used to be built from the foreign key: "repository:418". That
    # is the one thing about a runner an operator cannot look up in their head,
    # and it is the question this page exists to answer - which repository or
    # organization is this runner attached to. Resolve it to the name.
    #
    # A key pointing at something deleted says so rather than rendering a bare
    # number or, worse, an empty string that reads as "not attached".
    repo_names = repo_names or {}
    org_logins = org_logins or {}
    if value.enterprise_slug:
        scope_kind, scope_name, scope_url = "enterprise", value.enterprise_slug, None
    elif value.repo_id:
        scope_kind = "repository"
        resolved = repo_names.get(value.repo_id)
        scope_name = resolved or f"#{value.repo_id} (deleted repository)"
        scope_url = f"/ui/{resolved}" if resolved else None
    elif value.org_id:
        scope_kind = "organization"
        resolved = org_logins.get(value.org_id)
        scope_name = resolved or f"#{value.org_id} (deleted organization)"
        scope_url = f"/ui/orgs/{resolved}" if resolved else None
    else:
        scope_kind, scope_name, scope_url = "site", "All repositories", None
    scope = scope_name
    # Derived, not the stored column: a runner that stopped heartbeating is
    # offline whichever endpoint is asked. Serving value.status here is what
    # made this page report dozens of phantoms as online while the Actions
    # runner API, fixed first, reported them correctly — the same field
    # serialized from two places disagreeing with itself.
    status = _effective_status(value)
    return {
        "id": value.id, "name": value.name, "os": value.os, "status": status,
        "busy": value.busy and status == "online", "labels": value.labels or [],
        "scope": scope, "scope_kind": scope_kind, "scope_name": scope_name,
        "scope_url": scope_url, "last_heartbeat": _fmt_dt(value.last_heartbeat),
    }


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
    values = (await db.execute(select(Runner).order_by(Runner.id))).scalars().all()
    # Resolved in two queries rather than one per runner, and only for the ids
    # actually referenced.
    repo_ids = {r.repo_id for r in values if r.repo_id}
    org_ids = {r.org_id for r in values if r.org_id}
    repo_names = dict(
        (await db.execute(
            select(Repository.id, Repository.full_name).where(Repository.id.in_(repo_ids))
        )).all()
    ) if repo_ids else {}
    org_logins = dict(
        (await db.execute(
            select(Organization.id, Organization.login).where(Organization.id.in_(org_ids))
        )).all()
    ) if org_ids else {}
    return [_runner(item, repo_names, org_logins) for item in values]


@router.delete("/runners/{runner_id}", status_code=204)
async def delete_runner(runner_id: int, user: AuthUser, db: DbSession):
    """Remove a runner registration at any scope.

    The Actions API can delete a repository or enterprise runner, but a
    site-scoped one has none of those keys and so had no route at all — which
    is how a shim runner replaced a month ago stayed listed with nothing able
    to remove it.

    This reuses the Actions helper rather than deleting the row: jobs
    referencing the runner are detached (they keep runner_name, so the history
    still says what ran where), its sessions are removed, and a runner that is
    currently executing something is refused instead of stranded.
    """
    _require_admin(user)
    value = (await db.execute(select(Runner).where(Runner.id == runner_id))).scalar_one_or_none()
    if value is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await _delete_runner_and_detach_history(db, value)
    return Response(status_code=204)


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


# A run is "active" when it has not reached completed. The emulator uses
# queued and in_progress for runs, and queued, waiting and in_progress for
# jobs; listing the set explicitly rather than negating "completed" means a
# status nobody anticipated shows up here instead of being silently counted as
# finished.
_ACTIVE_RUN_STATUSES = ("queued", "in_progress", "waiting", "pending", "requested")


@router.get("/actions", response_model=list[AdminActiveRunResponse])
async def active_actions(user: AuthUser, db: DbSession, limit: int = 200):
    """Every unfinished workflow run, newest activity first.

    Actions state was only visible one repository at a time, which is the
    wrong shape for the question people actually have: what is running right
    now, and what is stuck. A run queued behind a missing runner label looks
    identical to one about to start unless you can see them together.
    """
    _require_admin(user)
    rows = (await db.execute(
        select(
            WorkflowRun.id, WorkflowRun.repo_id,
            WorkflowRun.event, WorkflowRun.status,
            WorkflowRun.head_branch, WorkflowRun.run_number,
            WorkflowRun.run_attempt, WorkflowRun.created_at,
            WorkflowRun.updated_at,
            Repository.full_name.label("repository"),
            Workflow.name.label("workflow"),
            User.login.label("actor"),
        )
        # Outer, deliberately. An inner join here dropped every run whose
        # repository had been deleted — on this stack that was 43 of 73
        # in-flight runs, and the page would have reported 30 and looked
        # complete. Deleting a repository does not delete its runs, so those
        # rows are exactly the ones an operator is least likely to know about.
        .outerjoin(Repository, Repository.id == WorkflowRun.repo_id)
        .outerjoin(Workflow, Workflow.id == WorkflowRun.workflow_id)
        .outerjoin(User, User.id == WorkflowRun.actor_id)
        .where(WorkflowRun.status.in_(_ACTIVE_RUN_STATUSES))
        .order_by(WorkflowRun.updated_at.desc(), WorkflowRun.id.desc())
        .limit(limit)
    )).all()
    if not rows:
        return []

    run_ids = [row.id for row in rows]
    jobs = (await db.execute(
        select(WorkflowJob).where(WorkflowJob.run_id.in_(run_ids))
    )).scalars().all()
    by_run: dict[int, list[WorkflowJob]] = {}
    for job in jobs:
        by_run.setdefault(job.run_id, []).append(job)

    results = []
    for row in rows:
        run_jobs = by_run.get(row.id, [])
        # Only the unfinished jobs are listed. On a stuck run those are the
        # ones that explain it, and a finished run's job list is available
        # from the run itself.
        active = [job for job in run_jobs if job.status != "completed"]
        # Named rather than blank: an empty repository column reads as a
        # display bug, while "#12 (deleted repository)" reads as the fact it
        # is — a run still queued against something that no longer exists.
        repository = row.repository or f"#{row.repo_id} (deleted repository)"
        results.append({
            "id": row.id,
            "repository": repository,
            "workflow": row.workflow or "(unknown workflow)",
            "event": row.event,
            "status": row.status,
            "head_branch": row.head_branch,
            "run_number": row.run_number,
            "run_attempt": row.run_attempt,
            "actor": row.actor,
            "created_at": _fmt_dt(row.created_at),
            "updated_at": _fmt_dt(row.updated_at),
            "jobs_total": len(run_jobs),
            "jobs_completed": sum(1 for job in run_jobs if job.status == "completed"),
            "active_jobs": [
                {
                    "id": job.id,
                    "name": job.name,
                    "status": job.status,
                    "runner_name": job.runner_name,
                    # The labels a job is waiting to be matched on. A job
                    # queued forever is usually queued on a label no runner
                    # registers, and that is invisible without this.
                    "labels": list(job.labels or []),
                    "started_at": _fmt_dt(job.started_at),
                }
                for job in sorted(active, key=lambda j: j.id)
            ],
            "url": (
                f"/ui/{row.repository}/actions/runs/{row.id}"
                if row.repository else None
            ),
        })
    return results


@router.post("/actions/{run_id}/cancel", status_code=204)
async def cancel_active_run(run_id: int, user: AuthUser, db: DbSession):
    """Cancel an unfinished run from the admin view.

    The Actions cancel endpoint is addressed by owner and repository, so it
    cannot reach a run whose repository was deleted — and those are most of
    the stale ones, because deleting a repository leaves its runs behind.
    Addressing by run id reaches all of them.

    Cancelling rather than deleting: the run and its jobs keep their history
    and simply stop being in flight, which is what GitHub does and what makes
    this safe to run over a backlog.
    """
    _require_admin(user)
    run = await cancel_workflow_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await db.commit()
    return Response(status_code=204)


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
