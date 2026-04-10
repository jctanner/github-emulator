"""Actions endpoints -- workflows, runs, jobs, secrets, variables."""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.actions import Workflow, WorkflowRun, WorkflowJob, Secret, Variable
from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id

router = APIRouter(tags=["actions"])

BASE = settings.BASE_URL


# --- Workflows ---

@router.get("/repos/{owner}/{repo}/actions/workflows")
async def list_workflows(
    owner: str, repo: str, db: DbSession, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List workflows."""
    repository = await get_repo_or_404(owner, repo, db)
    query = (
        select(Workflow)
        .where(Workflow.repo_id == repository.id)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    workflows = (await db.execute(query)).scalars().all()
    api = f"{BASE}/api/v3"
    items = []
    for w in workflows:
        items.append({
            "id": w.id,
            "node_id": _make_node_id("Workflow", w.id),
            "name": w.name,
            "path": w.path,
            "state": w.state,
            "created_at": _fmt_dt(w.created_at),
            "updated_at": _fmt_dt(w.updated_at),
            "url": f"{api}/repos/{owner}/{repo}/actions/workflows/{w.id}",
            "html_url": f"{BASE}/{owner}/{repo}/actions/workflows/{w.path}",
            "badge_url": f"{BASE}/{owner}/{repo}/workflows/{w.name}/badge.svg",
        })
    return {"total_count": len(items), "workflows": items}


@router.get("/repos/{owner}/{repo}/actions/workflows/{workflow_id}")
async def get_workflow(
    owner: str, repo: str, workflow_id: int, db: DbSession, current_user: CurrentUser,
):
    """Get a workflow."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.repo_id == repository.id)
    )
    w = result.scalar_one_or_none()
    if w is None:
        raise HTTPException(status_code=404, detail="Not Found")
    api = f"{BASE}/api/v3"
    return {
        "id": w.id, "name": w.name, "path": w.path, "state": w.state,
        "url": f"{api}/repos/{owner}/{repo}/actions/workflows/{w.id}",
        "created_at": _fmt_dt(w.created_at), "updated_at": _fmt_dt(w.updated_at),
    }


# --- Workflow runs ---

@router.get("/repos/{owner}/{repo}/actions/runs")
async def list_workflow_runs(
    owner: str, repo: str, db: DbSession, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List workflow runs."""
    repository = await get_repo_or_404(owner, repo, db)
    query = (
        select(WorkflowRun)
        .where(WorkflowRun.repo_id == repository.id)
        .order_by(WorkflowRun.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    runs = (await db.execute(query)).scalars().all()
    api = f"{BASE}/api/v3"
    items = []
    for r in runs:
        actor = SimpleUser.from_db(r.actor, BASE).model_dump() if r.actor else None
        items.append({
            "id": r.id,
            "name": r.workflow.name if r.workflow else "",
            "head_branch": r.head_branch,
            "head_sha": r.head_sha,
            "run_number": r.run_number,
            "run_attempt": r.run_attempt,
            "event": r.event,
            "status": r.status,
            "conclusion": r.conclusion,
            "workflow_id": r.workflow_id,
            "url": f"{api}/repos/{owner}/{repo}/actions/runs/{r.id}",
            "html_url": f"{BASE}/{owner}/{repo}/actions/runs/{r.id}",
            "created_at": _fmt_dt(r.created_at),
            "updated_at": _fmt_dt(r.updated_at),
            "actor": actor,
        })
    return {"total_count": len(items), "workflow_runs": items}


@router.get("/repos/{owner}/{repo}/actions/runs/{run_id}")
async def get_workflow_run(
    owner: str, repo: str, run_id: int, db: DbSession, current_user: CurrentUser,
):
    """Get a workflow run."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.repo_id == repository.id)
    )
    r = result.scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Not Found")
    api = f"{BASE}/api/v3"
    return {
        "id": r.id, "status": r.status, "conclusion": r.conclusion,
        "head_sha": r.head_sha, "head_branch": r.head_branch,
        "event": r.event, "run_number": r.run_number,
        "url": f"{api}/repos/{owner}/{repo}/actions/runs/{r.id}",
        "created_at": _fmt_dt(r.created_at), "updated_at": _fmt_dt(r.updated_at),
    }


# --- Workflow jobs ---

@router.get("/repos/{owner}/{repo}/actions/runs/{run_id}/jobs")
async def list_jobs(
    owner: str, repo: str, run_id: int, db: DbSession, current_user: CurrentUser,
):
    """List jobs for a workflow run."""
    repository = await get_repo_or_404(owner, repo, db)
    query = select(WorkflowJob).where(WorkflowJob.run_id == run_id)
    jobs = (await db.execute(query)).scalars().all()
    api = f"{BASE}/api/v3"
    items = []
    for j in jobs:
        items.append({
            "id": j.id, "name": j.name, "status": j.status,
            "conclusion": j.conclusion, "started_at": _fmt_dt(j.started_at),
            "completed_at": _fmt_dt(j.completed_at), "steps": j.steps or [],
            "url": f"{api}/repos/{owner}/{repo}/actions/jobs/{j.id}",
        })
    return {"total_count": len(items), "jobs": items}


# --- Secrets ---

@router.get("/repos/{owner}/{repo}/actions/secrets")
async def list_secrets(
    owner: str, repo: str, db: DbSession, user: AuthUser,
):
    """List repository secrets (names only, not values)."""
    repository = await get_repo_or_404(owner, repo, db)
    query = select(Secret).where(Secret.repo_id == repository.id)
    secrets = (await db.execute(query)).scalars().all()
    return {
        "total_count": len(secrets),
        "secrets": [
            {
                "name": s.name,
                "created_at": _fmt_dt(s.created_at),
                "updated_at": _fmt_dt(s.updated_at),
            }
            for s in secrets
        ],
    }


@router.get("/repos/{owner}/{repo}/actions/secrets/{secret_name}")
async def get_secret(
    owner: str, repo: str, secret_name: str, db: DbSession, user: AuthUser,
):
    """Get a repository secret (name only)."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Secret).where(Secret.repo_id == repository.id, Secret.name == secret_name)
    )
    s = result.scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return {"name": s.name, "created_at": _fmt_dt(s.created_at), "updated_at": _fmt_dt(s.updated_at)}


@router.put("/repos/{owner}/{repo}/actions/secrets/{secret_name}", status_code=201)
async def create_or_update_secret(
    owner: str, repo: str, secret_name: str, body: dict, user: AuthUser, db: DbSession,
):
    """Create or update a repository secret."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Secret).where(Secret.repo_id == repository.id, Secret.name == secret_name)
    )
    s = result.scalar_one_or_none()
    if s is None:
        s = Secret(repo_id=repository.id, name=secret_name)
        db.add(s)
    await db.commit()
    return {"name": secret_name}


@router.delete("/repos/{owner}/{repo}/actions/secrets/{secret_name}", status_code=204)
async def delete_secret(
    owner: str, repo: str, secret_name: str, user: AuthUser, db: DbSession,
):
    """Delete a repository secret."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Secret).where(Secret.repo_id == repository.id, Secret.name == secret_name)
    )
    s = result.scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await db.delete(s)
    await db.commit()


# --- Variables ---

@router.get("/repos/{owner}/{repo}/actions/variables")
async def list_variables(
    owner: str, repo: str, db: DbSession, user: AuthUser,
):
    """List repository variables."""
    repository = await get_repo_or_404(owner, repo, db)
    query = select(Variable).where(Variable.repo_id == repository.id)
    variables = (await db.execute(query)).scalars().all()
    return {
        "total_count": len(variables),
        "variables": [
            {"name": v.name, "value": v.value, "created_at": _fmt_dt(v.created_at), "updated_at": _fmt_dt(v.updated_at)}
            for v in variables
        ],
    }


@router.post("/repos/{owner}/{repo}/actions/variables", status_code=201)
async def create_variable(
    owner: str, repo: str, body: dict, user: AuthUser, db: DbSession,
):
    """Create a repository variable."""
    repository = await get_repo_or_404(owner, repo, db)
    name = body.get("name", "")
    value = body.get("value", "")
    if not name:
        raise HTTPException(status_code=422, detail="name is required")

    v = Variable(repo_id=repository.id, name=name, value=value)
    db.add(v)
    await db.commit()
    return {"name": name, "value": value}


@router.patch("/repos/{owner}/{repo}/actions/variables/{variable_name}")
async def update_variable(
    owner: str, repo: str, variable_name: str, body: dict, user: AuthUser, db: DbSession,
):
    """Update a repository variable."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Variable).where(Variable.repo_id == repository.id, Variable.name == variable_name)
    )
    v = result.scalar_one_or_none()
    if v is None:
        raise HTTPException(status_code=404, detail="Not Found")

    if "value" in body:
        v.value = body["value"]
    if "name" in body:
        v.name = body["name"]

    await db.commit()
    return {"name": v.name, "value": v.value}


@router.delete("/repos/{owner}/{repo}/actions/variables/{variable_name}", status_code=204)
async def delete_variable(
    owner: str, repo: str, variable_name: str, user: AuthUser, db: DbSession,
):
    """Delete a repository variable."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Variable).where(Variable.repo_id == repository.id, Variable.name == variable_name)
    )
    v = result.scalar_one_or_none()
    if v is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await db.delete(v)
    await db.commit()
