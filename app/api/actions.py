"""Actions endpoints -- workflows, runs, jobs, secrets, variables."""

import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select, func, or_

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.actions import Workflow, WorkflowRun, WorkflowJob, Secret, Variable
from app.models.artifact import WorkflowArtifact
from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id

router = APIRouter(tags=["actions"])

BASE = settings.BASE_URL


def _workflow_json(w: Workflow, owner: str, repo: str) -> dict:
    api = f"{BASE}/api/v3"
    return {
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
    }


def _run_json(r: WorkflowRun, owner: str, repo: str) -> dict:
    api = f"{BASE}/api/v3"
    actor = SimpleUser.from_db(r.actor, BASE).model_dump() if r.actor else None
    return {
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
    }


def _job_json(j: WorkflowJob, owner: str, repo: str) -> dict:
    api = f"{BASE}/api/v3"
    return {
        "id": j.id,
        "run_id": j.run_id,
        "name": j.name,
        "workflow_name": j.workflow_name,
        "status": j.status,
        "conclusion": j.conclusion,
        "started_at": _fmt_dt(j.started_at),
        "completed_at": _fmt_dt(j.completed_at),
        "steps": j.steps or [],
        "runner_name": j.runner_name,
        "runner_id": j.runner_id,
        "labels": j.labels or [],
        "run_attempt": j.run_attempt,
        "needs": j.needs or [],
        "permissions": j.permissions or {},
        "url": f"{api}/repos/{owner}/{repo}/actions/jobs/{j.id}",
        "html_url": f"{BASE}/{owner}/{repo}/actions/jobs/{j.id}",
        "logs_url": f"{api}/repos/{owner}/{repo}/actions/jobs/{j.id}/logs",
    }


def _job_log_path(job_id: int) -> str:
    return os.path.join(settings.DATA_DIR, "logs", "jobs", f"{job_id}.log")


def _artifact_json(artifact: WorkflowArtifact, owner: str, repo: str) -> dict:
    api = f"{BASE}/api/v3"
    return {
        "id": artifact.id,
        "node_id": _make_node_id("Artifact", artifact.id),
        "name": artifact.name,
        "size_in_bytes": artifact.size_in_bytes,
        "archive_download_url": f"{api}/repos/{owner}/{repo}/actions/artifacts/{artifact.id}/{artifact.name}",
        "expired": artifact.expired,
        "created_at": _fmt_dt(artifact.created_at),
        "workflow_run": {"id": artifact.run_id},
    }


@router.get("/repos/{owner}/{repo}/actions/runs/{run_id}/artifacts")
async def list_artifacts(owner: str, repo: str, run_id: int, user: AuthUser, db: DbSession):
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(select(WorkflowArtifact).where(WorkflowArtifact.repo_id == repository.id, WorkflowArtifact.run_id == run_id))
    artifacts = result.scalars().all()
    return {"total_count": len(artifacts), "artifacts": [_artifact_json(item, owner, repo) for item in artifacts]}


@router.post("/repos/{owner}/{repo}/actions/runs/{run_id}/artifacts", status_code=201)
async def upload_artifact(owner: str, repo: str, run_id: int, body: dict, user: AuthUser, db: DbSession):
    repository = await get_repo_or_404(owner, repo, db)
    run = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.repo_id == repository.id))).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    name = str(body.get("name", "")).strip()
    files = body.get("files", {})
    if not name or not isinstance(files, dict):
        raise HTTPException(status_code=422, detail="name and files are required")
    import json
    artifact = WorkflowArtifact(run_id=run_id, repo_id=repository.id, name=name, files=files, size_in_bytes=len(json.dumps(files, separators=(",", ":"))))
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return _artifact_json(artifact, owner, repo)


@router.get("/repos/{owner}/{repo}/actions/artifacts/{artifact_id}")
async def get_artifact(owner: str, repo: str, artifact_id: int, user: AuthUser, db: DbSession):
    repository = await get_repo_or_404(owner, repo, db)
    artifact = (await db.execute(select(WorkflowArtifact).where(WorkflowArtifact.id == artifact_id, WorkflowArtifact.repo_id == repository.id))).scalar_one_or_none()
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {**_artifact_json(artifact, owner, repo), "files": artifact.files or {}}


@router.delete("/repos/{owner}/{repo}/actions/artifacts/{artifact_id}", status_code=204)
async def delete_artifact(owner: str, repo: str, artifact_id: int, user: AuthUser, db: DbSession):
    repository = await get_repo_or_404(owner, repo, db)
    artifact = (await db.execute(select(WorkflowArtifact).where(WorkflowArtifact.id == artifact_id, WorkflowArtifact.repo_id == repository.id))).scalar_one_or_none()
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    await db.delete(artifact)
    await db.commit()


def _check_read_access(repository, current_user) -> None:
    if repository.private and (
        current_user is None
        or (current_user.id != repository.owner_id and not current_user.site_admin)
    ):
        raise HTTPException(status_code=404, detail="Not Found")


# --- Workflows ---

@router.get("/repos/{owner}/{repo}/actions/workflows")
async def list_workflows(
    owner: str, repo: str, db: DbSession, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List workflows."""
    repository = await get_repo_or_404(owner, repo, db)
    _check_read_access(repository, current_user)
    from app.services.workflow_service import materialize_reusable_workflows, sync_workflows_to_db

    await sync_workflows_to_db(db, repository, "HEAD")
    await db.commit()
    query = (
        select(Workflow)
        .where(Workflow.repo_id == repository.id)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    workflows = (await db.execute(query)).scalars().all()
    items = [_workflow_json(w, owner, repo) for w in workflows]
    return {"total_count": len(items), "workflows": items}


@router.get("/repos/{owner}/{repo}/actions/workflows/{workflow_id}")
async def get_workflow(
    owner: str, repo: str, workflow_id: int, db: DbSession, current_user: CurrentUser,
):
    """Get a workflow."""
    repository = await get_repo_or_404(owner, repo, db)
    _check_read_access(repository, current_user)
    from app.services.workflow_service import sync_workflows_to_db

    await sync_workflows_to_db(db, repository, "HEAD")
    await db.commit()
    result = await db.execute(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.repo_id == repository.id)
    )
    w = result.scalar_one_or_none()
    if w is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _workflow_json(w, owner, repo)


@router.post("/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches", status_code=204)
async def dispatch_workflow(
    owner: str, repo: str, workflow_id: str, body: dict, user: AuthUser, db: DbSession,
):
    """Dispatch a workflow configured with ``workflow_dispatch``."""
    repository = await get_repo_or_404(owner, repo, db)
    from app.services.workflow_service import sync_workflows_to_db

    await sync_workflows_to_db(db, repository, "HEAD")
    await db.commit()
    workflow_query = select(Workflow).where(Workflow.repo_id == repository.id)
    if workflow_id.isdigit():
        workflow_query = workflow_query.where(Workflow.id == int(workflow_id))
    else:
        # ``gh workflow run`` accepts either the stored workflow path or the
        # short filename (for example ``triage.yml``).  GitHub resolves the
        # latter beneath .github/workflows; preserve that behavior here.
        short_path = workflow_id
        if "/" not in short_path:
            short_path = f".github/workflows/{short_path}"
        workflow_query = workflow_query.where(
            or_(Workflow.path == workflow_id, Workflow.path == short_path)
        )
    workflow = (await db.execute(workflow_query)).scalar_one_or_none()

    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    from app.services.workflow_service import (
        create_workflow_run,
        detect_workflows,
        evaluate_trigger,
        get_ref_sha,
        materialize_reusable_workflows,
    )

    workflow_yaml = next(
        (item for item in await detect_workflows(repository.disk_path or "")
         if item.get("_path") == workflow.path),
        None,
    )
    if workflow_yaml is None or not evaluate_trigger(workflow_yaml, "workflow_dispatch", {}):
        raise HTTPException(status_code=422, detail="Workflow is not dispatchable")

    ref = str(body.get("ref") or repository.default_branch or "main")
    ref_name = ref.removeprefix("refs/heads/")
    head_sha = await get_ref_sha(repository.disk_path or "", ref_name)
    if not head_sha:
        raise HTTPException(status_code=422, detail=f"Unknown ref: {ref}")

    payload = {
        "ref": f"refs/heads/{ref_name}",
        "after": head_sha,
        "inputs": body.get("inputs") or {},
        "repository": {"id": repository.id, "full_name": repository.full_name},
        "sender": {"login": user.login, "id": user.id},
    }
    workflow_yaml = await materialize_reusable_workflows(
        workflow_yaml,
        repository.disk_path or "",
        ref_name,
        db,
        inputs=payload.get("inputs", {}),
        secrets=payload.get("secrets", {}),
    )
    await create_workflow_run(
        db, workflow, workflow_yaml, "workflow_dispatch", payload,
        user, head_sha, ref_name,
    )
    await db.commit()
    return Response(status_code=204)


# --- Workflow runs ---

@router.get("/repos/{owner}/{repo}/actions/runs")
async def list_workflow_runs(
    owner: str, repo: str, db: DbSession, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List workflow runs."""
    repository = await get_repo_or_404(owner, repo, db)
    _check_read_access(repository, current_user)
    query = (
        select(WorkflowRun)
        .where(WorkflowRun.repo_id == repository.id)
        .order_by(WorkflowRun.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    runs = (await db.execute(query)).scalars().all()
    items = [_run_json(r, owner, repo) for r in runs]
    return {"total_count": len(items), "workflow_runs": items}


@router.get("/repos/{owner}/{repo}/actions/runs/{run_id}")
async def get_workflow_run(
    owner: str, repo: str, run_id: int, db: DbSession, current_user: CurrentUser,
):
    """Get a workflow run."""
    repository = await get_repo_or_404(owner, repo, db)
    _check_read_access(repository, current_user)
    result = await db.execute(
        select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.repo_id == repository.id)
    )
    r = result.scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _run_json(r, owner, repo)


@router.post("/repos/{owner}/{repo}/actions/runs/{run_id}/cancel", status_code=202)
async def cancel_workflow_run(
    owner: str, repo: str, run_id: int, db: DbSession, user: AuthUser,
):
    """Cancel a workflow run."""
    repository = await get_repo_or_404(owner, repo, db)
    from app.services.workflow_service import cancel_workflow_run as do_cancel
    run = await do_cancel(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await db.commit()
    return {}


@router.post("/repos/{owner}/{repo}/actions/runs/{run_id}/rerun", status_code=201)
async def rerun_workflow(
    owner: str, repo: str, run_id: int, db: DbSession, user: AuthUser,
):
    """Re-run a workflow."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.repo_id == repository.id)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Not Found")

    count = (await db.execute(
        select(func.count(WorkflowRun.id)).where(
            WorkflowRun.workflow_id == run.workflow_id
        )
    )).scalar() or 0

    new_run = WorkflowRun(
        workflow_id=run.workflow_id,
        repo_id=run.repo_id,
        head_sha=run.head_sha,
        head_branch=run.head_branch,
        event=run.event,
        status="queued",
        run_number=count + 1,
        run_attempt=run.run_attempt + 1,
        actor_id=user.id,
        trigger_payload=run.trigger_payload,
    )
    db.add(new_run)
    await db.flush()

    old_jobs = (await db.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run_id)
    )).scalars().all()

    for old_job in old_jobs:
        new_job = WorkflowJob(
            run_id=new_run.id,
            name=old_job.name,
            workflow_name=old_job.workflow_name,
            status="queued" if not old_job.needs else "waiting",
            steps=[
                {
                    **s,
                    "status": "queued",
                    "conclusion": None,
                }
                for s in (old_job.steps or [])
            ],
            labels=old_job.labels,
            run_attempt=new_run.run_attempt,
            needs=old_job.needs,
        )
        db.add(new_job)

    await db.commit()
    api = f"{BASE}/api/v3"
    return {
        "id": new_run.id, "status": new_run.status,
        "run_number": new_run.run_number, "run_attempt": new_run.run_attempt,
        "url": f"{api}/repos/{owner}/{repo}/actions/runs/{new_run.id}",
    }


# --- Workflow jobs ---

@router.get("/repos/{owner}/{repo}/actions/runs/{run_id}/jobs")
async def list_jobs(
    owner: str, repo: str, run_id: int, db: DbSession, current_user: CurrentUser,
):
    """List jobs for a workflow run."""
    repository = await get_repo_or_404(owner, repo, db)
    _check_read_access(repository, current_user)
    run_result = await db.execute(
        select(WorkflowRun).where(
            WorkflowRun.id == run_id,
            WorkflowRun.repo_id == repository.id,
        )
    )
    if run_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Not Found")

    query = select(WorkflowJob).where(WorkflowJob.run_id == run_id)
    jobs = (await db.execute(query)).scalars().all()
    items = [_job_json(j, owner, repo) for j in jobs]
    return {"total_count": len(items), "jobs": items}


@router.get("/repos/{owner}/{repo}/actions/jobs/{job_id}")
async def get_job(
    owner: str, repo: str, job_id: int, db: DbSession, current_user: CurrentUser,
):
    """Get a workflow job."""
    repository = await get_repo_or_404(owner, repo, db)
    _check_read_access(repository, current_user)
    result = await db.execute(
        select(WorkflowJob)
        .join(WorkflowRun, WorkflowJob.run_id == WorkflowRun.id)
        .where(
            WorkflowJob.id == job_id,
            WorkflowRun.repo_id == repository.id,
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _job_json(job, owner, repo)


@router.get("/repos/{owner}/{repo}/actions/jobs/{job_id}/logs")
async def get_job_logs(
    owner: str, repo: str, job_id: int, db: DbSession, current_user: CurrentUser,
):
    """Get captured logs for a workflow job."""
    repository = await get_repo_or_404(owner, repo, db)
    _check_read_access(repository, current_user)
    result = await db.execute(
        select(WorkflowJob)
        .join(WorkflowRun, WorkflowJob.run_id == WorkflowRun.id)
        .where(
            WorkflowJob.id == job_id,
            WorkflowRun.repo_id == repository.id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Not Found")

    log_path = _job_log_path(job_id)
    if not os.path.isfile(log_path):
        raise HTTPException(status_code=404, detail="Logs not found")
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        return PlainTextResponse(f.read())


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
    if "value" in body or "plaintext" in body:
        s.value = str(body.get("value", body.get("plaintext", "")))
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
