"""Custom runner job dispatch protocol -- long-poll, progress, completion."""

import asyncio
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select

from app.api.deps import AuthUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.actions import EnterpriseRunnerRegistrationToken, Runner, RegistrationToken, WorkflowJob, WorkflowRun
from app.models.repository import Repository
from app.services.auth_service import hash_token
from app.services.workflow_service import check_run_completion, dispatch_ready_jobs

router = APIRouter(tags=["actions-dispatch"])


def _is_expired(value: datetime) -> bool:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value < datetime.now(timezone.utc)


async def _get_runner_from_token(request: Request, db) -> Runner:
    """Authenticate a runner by its bearer token."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Runner token required")
    token = auth[7:]
    token_hash = hash_token(token)
    result = await db.execute(
        select(Runner).where(Runner.token_hash == token_hash)
    )
    runner = result.scalar_one_or_none()
    if runner is None:
        raise HTTPException(status_code=401, detail="Invalid runner token")
    return runner


async def _body_or_empty(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raw = await request.body()
        parsed = parse_qs(raw.decode()) if raw else {}
        body = {key: values[-1] for key, values in parsed.items() if values}
    return body if isinstance(body, dict) else {}


def _registration_token_from_request(request: Request, body: dict) -> str:
    token = (
        body.get("token")
        or body.get("registrationToken")
        or request.query_params.get("token")
        or request.query_params.get("registrationToken")
        or request.headers.get("X-GitHub-Runner-Registration-Token")
        or request.headers.get("X-Runner-Registration-Token")
        or ""
    )
    auth = request.headers.get("Authorization", "")
    if not token and auth.startswith("token "):
        token = auth[6:]
    if not token and auth.startswith("Bearer "):
        token = auth[7:]
    if not token and " " in auth:
        token = auth.split(None, 1)[1]
    return token


async def _create_runner_from_registration(
    reg_token: str,
    body: dict,
    db,
) -> tuple[Runner, str]:
    result = await db.execute(
        select(RegistrationToken).where(RegistrationToken.token == reg_token)
    )
    reg = result.scalar_one_or_none()
    enterprise_reg = None
    if reg is None:
        result = await db.execute(
            select(EnterpriseRunnerRegistrationToken).where(
                EnterpriseRunnerRegistrationToken.token == reg_token
            )
        )
        enterprise_reg = result.scalar_one_or_none()
    if reg is None and enterprise_reg is None:
        raise HTTPException(status_code=401, detail="Invalid registration token")

    registration = reg or enterprise_reg
    if _is_expired(registration.expires_at):
        raise HTTPException(status_code=401, detail="Registration token expired")

    labels = body.get("labels", ["self-hosted", "linux"])
    if isinstance(labels, list):
        labels = [
            str(label.get("name", "")) if isinstance(label, dict) else str(label)
            for label in labels
        ]
        labels = [label for label in labels if label]
    else:
        labels = ["self-hosted", "linux"]

    runner_token = f"ghp_runner_{secrets.token_urlsafe(32)}"
    runner = Runner(
        name=body.get("name", body.get("agentName", "unnamed-runner")),
        os=body.get("os", body.get("osDescription", "linux")),
        status="online",
        labels=labels,
        busy=False,
        token_hash=hash_token(runner_token),
        repo_id=reg.repo_id if reg is not None else None,
        enterprise_slug=(
            enterprise_reg.enterprise_slug if enterprise_reg is not None else None
        ),
        last_heartbeat=datetime.now(timezone.utc),
    )
    db.add(runner)
    await db.delete(registration)
    await db.commit()
    await db.refresh(runner)
    return runner, runner_token


async def _requeue_stale_jobs(db) -> int:
    """Return jobs from runners that have stopped heartbeating to the queue."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.RUNNER_STALE_THRESHOLD_SECONDS
    )
    result = await db.execute(
        select(WorkflowJob, Runner)
        .join(Runner, WorkflowJob.runner_id == Runner.id)
        .where(
            WorkflowJob.status == "in_progress",
            Runner.last_heartbeat.is_not(None),
            Runner.last_heartbeat < cutoff,
        )
    )
    stale_jobs = result.all()
    for job, runner in stale_jobs:
        job.status = "queued"
        job.runner_id = None
        job.runner_name = None
        job.started_at = None
        job.steps = [
            {**step, "status": "queued", "conclusion": None}
            for step in (job.steps or [])
        ]
        runner.status = "offline"
        runner.busy = False
    if stale_jobs:
        await db.commit()
    return len(stale_jobs)


def _registration_response(runner: Runner, runner_token: str, base: str | None = None) -> dict:
    base = base or settings.BASE_URL
    return {
        "id": runner.id,
        "agentId": runner.id,
        "poolId": 1,
        "poolName": "Default",
        "name": runner.name,
        "token": runner_token,
        "tokenSchema": "OAuthAccessToken",
        "token_schema": "OAuthAccessToken",
        "serverUrl": base,
        "gitServerUrl": base,
        "tenantUrl": base,
        "url": base,
        "pipelines_url": f"{base}/_services/pipelines",
        "actionsServiceUrl": f"{base}/_apis/distributedtask",
        "authorization": {
            "scheme": "OAuth",
            "parameters": {"AccessToken": runner_token},
        },
        "runner": {
            "id": runner.id,
            "name": runner.name,
            "os": runner.os,
            "status": runner.status,
            "labels": runner.labels or [],
        },
    }


def _runner_labels(body: dict) -> list[str]:
    labels = body.get("labels", ["self-hosted", "linux"])
    if not isinstance(labels, list):
        return ["self-hosted", "linux"]
    normalized = [
        str(label.get("name", "")) if isinstance(label, dict) else str(label)
        for label in labels
    ]
    return [label for label in normalized if label]


@router.post("/actions/runner/register")
async def register_runner(body: dict, db: DbSession):
    """Register a new runner using a registration token."""
    reg_token = body.get("token", "")
    runner, runner_token = await _create_runner_from_registration(reg_token, body, db)

    return {
        "runner_id": runner.id,
        "token": runner_token,
        "name": runner.name,
    }


@router.post("/admin/actions/runners/register")
async def register_site_wide_runner(body: dict, user: AuthUser, db: DbSession):
    """Register or replace an emulator-wide custom runner."""
    if not user.site_admin:
        raise HTTPException(status_code=403, detail="Site administrator required")

    name = str(body.get("name") or "site-wide-runner").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Runner name is required")

    runner_token = f"ghp_runner_{secrets.token_urlsafe(32)}"
    result = await db.execute(
        select(Runner).where(
            Runner.name == name,
            Runner.repo_id.is_(None),
            Runner.org_id.is_(None),
        )
    )
    runner = result.scalar_one_or_none()
    if runner is None:
        runner = Runner(name=name, repo_id=None, org_id=None)
        db.add(runner)

    runner.os = str(body.get("os") or "linux")
    runner.status = "online"
    runner.labels = _runner_labels(body)
    runner.busy = False
    runner.token_hash = hash_token(runner_token)
    runner.last_heartbeat = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(runner)
    return {
        "runner_id": runner.id,
        "token": runner_token,
        "name": runner.name,
        "scope": "site",
    }


@router.post("/actions/runner-registration")
async def register_runner_for_actions_service(request: Request, db: DbSession):
    """Compatibility broker used by the upstream actions/runner config flow."""
    body = await _body_or_empty(request)
    reg_token = _registration_token_from_request(request, body)
    if not reg_token:
        raise HTTPException(status_code=401, detail="Registration token required")
    runner, runner_token = await _create_runner_from_registration(reg_token, body, db)
    request_base = f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"
    server_url = str(body.get("url") or request_base).rstrip("/")
    return _registration_response(runner, runner_token, server_url)


@router.post("/actions/runner/heartbeat")
async def runner_heartbeat(request: Request, db: DbSession):
    """Runner sends periodic heartbeats to stay online."""
    runner = await _get_runner_from_token(request, db)
    runner.last_heartbeat = datetime.now(timezone.utc)
    runner.status = "online"
    await db.commit()
    return {"status": "ok"}


@router.get("/repos/{owner}/{repo}/actions/runner/jobs")
async def poll_for_jobs(
    owner: str, repo: str,
    request: Request,
    db: DbSession,
    labels: str = Query("self-hosted,linux"),
    timeout: int = Query(30, ge=1, le=60),
):
    """Long-poll for available jobs matching runner labels."""
    runner = await _get_runner_from_token(request, db)
    repository = await get_repo_or_404(owner, repo, db)
    if runner.repo_id != repository.id:
        raise HTTPException(
            status_code=403,
            detail="Runner is not registered for this repository",
        )
    return await _poll_for_jobs(
        runner,
        db,
        labels=labels,
        timeout=timeout,
        repository_id=repository.id,
    )


@router.get("/actions/runner/jobs")
async def poll_for_site_wide_jobs(
    request: Request,
    db: DbSession,
    labels: str = Query("self-hosted,linux"),
    timeout: int = Query(30, ge=1, le=60),
):
    """Long-poll across repositories using a site-wide runner token."""
    runner = await _get_runner_from_token(request, db)
    if runner.repo_id is not None or runner.org_id is not None:
        raise HTTPException(status_code=403, detail="Site-wide runner required")
    return await _poll_for_jobs(
        runner,
        db,
        labels=labels,
        timeout=timeout,
        repository_id=None,
    )


async def _poll_for_jobs(
    runner: Runner,
    db,
    *,
    labels: str,
    timeout: int,
    repository_id: int | None,
):
    """Claim the oldest queued job in the runner's permitted scope."""
    runner_labels = {label.strip().lower() for label in labels.split(",") if label.strip()}

    await _requeue_stale_jobs(db)

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        query = (
            select(WorkflowJob)
            .join(WorkflowRun, WorkflowJob.run_id == WorkflowRun.id)
            .where(
                WorkflowRun.status.in_(("queued", "in_progress")),
                WorkflowJob.status == "queued",
            )
            .order_by(WorkflowJob.created_at)
        )
        if repository_id is not None:
            query = query.where(WorkflowRun.repo_id == repository_id)
        result = await db.execute(query)
        jobs = result.scalars().all()

        for job in jobs:
            job_labels = {
                str(label).strip().lower()
                for label in (job.labels or ["self-hosted"])
                if str(label).strip()
            }
            if job_labels.issubset(runner_labels):
                job.status = "in_progress"
                job.runner_id = runner.id
                job.runner_name = runner.name
                job.started_at = datetime.now(timezone.utc)

                runner.busy = True
                runner.status = "busy"
                await db.commit()

                run_result = await db.execute(
                    select(WorkflowRun).where(WorkflowRun.id == job.run_id)
                )
                run = run_result.scalar_one_or_none()
                repository = None
                if run is not None:
                    repository = (
                        await db.execute(
                            select(Repository).where(Repository.id == run.repo_id)
                        )
                    ).scalar_one_or_none()
                if run and run.status == "queued":
                    run.status = "in_progress"
                    await db.commit()

                return {
                    "job_id": job.id,
                    "run_id": job.run_id,
                    "name": job.name,
                    "steps": job.steps or [],
                    "labels": job.labels,
                    "workflow_name": job.workflow_name,
                    "env": {},
                    "event": run.event if run else "workflow_dispatch",
                    "event_payload": run.trigger_payload if run else {},
                    "head_sha": run.head_sha if run else "",
                    "head_branch": run.head_branch if run else "main",
                    "run_number": run.run_number if run else job.run_id,
                    "repository": repository.full_name if repository else "",
                }

        await asyncio.sleep(2)
        # AsyncSession.expire_all is synchronous; awaiting it raises a
        # TypeError whenever the runner has no queued job during long-polling.
        db.expire_all()

    return Response(status_code=204)


@router.patch("/repos/{owner}/{repo}/actions/runner/jobs/{job_id}")
async def update_job_progress(
    owner: str, repo: str, job_id: int,
    body: dict, request: Request, db: DbSession,
):
    """Runner reports step progress."""
    runner = await _get_runner_from_token(request, db)
    result = await db.execute(
        select(WorkflowJob).where(WorkflowJob.id == job_id, WorkflowJob.runner_id == runner.id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if "steps" in body:
        job.steps = body["steps"]

    await db.commit()
    return {"status": "updated"}


@router.post("/repos/{owner}/{repo}/actions/runner/jobs/{job_id}/complete")
async def complete_job(
    owner: str, repo: str, job_id: int,
    body: dict, request: Request, db: DbSession,
):
    """Runner reports job completion."""
    runner = await _get_runner_from_token(request, db)
    result = await db.execute(
        select(WorkflowJob).where(WorkflowJob.id == job_id, WorkflowJob.runner_id == runner.id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # A runner may finish a process after its workflow was canceled.  Preserve
    # the cancellation result instead of allowing that late callback to turn
    # the job back into a successful execution.
    if job.status == "completed" and job.conclusion == "cancelled":
        runner.busy = False
        runner.status = "online"
        await db.commit()
        return {"status": "completed", "conclusion": "cancelled"}

    conclusion = body.get("conclusion", "success")
    job.status = "completed"
    job.conclusion = conclusion
    job.completed_at = datetime.now(timezone.utc)

    if "steps" in body:
        job.steps = body["steps"]

    runner.busy = False
    runner.status = "online"
    await db.commit()

    await dispatch_ready_jobs(db, job.run_id)
    await check_run_completion(db, job.run_id)
    await db.commit()

    return {"status": "completed", "conclusion": conclusion}


@router.post("/repos/{owner}/{repo}/actions/runner/jobs/{job_id}/logs")
async def upload_job_logs(
    owner: str, repo: str, job_id: int,
    request: Request, db: DbSession,
):
    """Accept log upload from runner."""
    runner = await _get_runner_from_token(request, db)
    result = await db.execute(
        select(WorkflowJob).where(WorkflowJob.id == job_id, WorkflowJob.runner_id == runner.id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    log_data = await request.body()
    log_dir = os.path.join(settings.DATA_DIR, "logs", "jobs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{job_id}.log")
    with open(log_path, "ab") as f:
        f.write(log_data)

    return {"status": "ok"}
