"""GHES-internal pipelines endpoints for real actions/runner binary compatibility.

The real runner uses /_services/pipelines/ paths during registration
and configuration. These endpoints implement enough of the protocol
for the runner's config.sh to succeed.
"""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.api.deps import DbSession, get_repo_or_404
from app.config import settings
from app.models.actions import EnterpriseRunnerRegistrationToken, Runner, RegistrationToken
from app.services.auth_service import hash_token

router = APIRouter(tags=["actions-pipelines"])


def _is_expired(value: datetime) -> bool:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value < datetime.now(timezone.utc)


def _request_base(request: Request) -> str:
    return settings.BASE_URL or f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"


@router.post("/_services/pipelines/enterprises/{enterprise}/_apis/pipelines/runs/register")
async def pipelines_register_enterprise_runner(
    enterprise: str, request: Request, db: DbSession,
):
    """Register an upstream actions/runner at enterprise scope."""
    if enterprise != settings.ENTERPRISE_SLUG:
        raise HTTPException(status_code=404, detail="Not Found")
    body = await request.json()
    reg_token = body.get("token", "")
    result = await db.execute(
        select(EnterpriseRunnerRegistrationToken).where(
            EnterpriseRunnerRegistrationToken.token == reg_token,
            EnterpriseRunnerRegistrationToken.enterprise_slug == enterprise,
        )
    )
    registration = result.scalar_one_or_none()
    if registration is None:
        raise HTTPException(status_code=401, detail="Invalid registration token")
    if _is_expired(registration.expires_at):
        raise HTTPException(status_code=401, detail="Registration token expired")

    labels_raw = body.get("labels", [])
    labels = [
        label.get("name", str(label)) if isinstance(label, dict) else str(label)
        for label in labels_raw
    ] if isinstance(labels_raw, list) else ["self-hosted"]
    runner_token = f"ghp_runner_{secrets.token_urlsafe(32)}"
    runner = Runner(
        name=body.get("name", body.get("agentName", "runner")),
        os=body.get("os", "linux"),
        status="online",
        labels=labels,
        busy=False,
        token_hash=hash_token(runner_token),
        enterprise_slug=enterprise,
        last_heartbeat=datetime.now(timezone.utc),
    )
    db.add(runner)
    await db.delete(registration)
    await db.commit()
    await db.refresh(runner)
    base = _request_base(request)
    return {
        "id": runner.id,
        "name": runner.name,
        "token": runner_token,
        "serverUrl": base,
        "gitServerUrl": base,
        "pipelines_url": f"{base}/_services/pipelines",
        "actionsServiceUrl": f"{base}/_apis/distributedtask",
    }


@router.delete("/_services/pipelines/enterprises/{enterprise}/_apis/pipelines/runs/{runner_id}")
async def pipelines_deregister_enterprise_runner(
    enterprise: str, runner_id: int, db: DbSession,
):
    result = await db.execute(select(Runner).where(
        Runner.id == runner_id,
        Runner.enterprise_slug == enterprise,
    ))
    runner = result.scalar_one_or_none()
    if runner is None:
        raise HTTPException(status_code=404, detail="Runner not found")
    await db.delete(runner)
    await db.commit()
    return {"status": "removed"}


@router.get("/_services/pipelines/enterprises/{enterprise}/_apis/pipelines/runs")
async def pipelines_list_enterprise_runners(enterprise: str, db: DbSession):
    result = await db.execute(
        select(Runner).where(Runner.enterprise_slug == enterprise)
    )
    runners = result.scalars().all()
    return {
        "count": len(runners),
        "value": [
            {"id": r.id, "name": r.name, "status": r.status, "os": r.os}
            for r in runners
        ],
    }


@router.post("/_services/pipelines/{owner}/{repo}/_apis/pipelines/runs/register")
async def pipelines_register_runner(
    owner: str, repo: str, request: Request, db: DbSession,
):
    """Runner registration via GHES pipelines protocol.

    The real runner sends an RSA public key and runner configuration.
    We validate the registration token from the query string or body
    and create a runner record.
    """
    body = await request.json()
    repository = await get_repo_or_404(owner, repo, db)

    reg_token = body.get("token", "")
    result = await db.execute(
        select(RegistrationToken).where(RegistrationToken.token == reg_token)
    )
    reg = result.scalar_one_or_none()
    if reg is None:
        raise HTTPException(status_code=401, detail="Invalid registration token")
    if _is_expired(reg.expires_at):
        raise HTTPException(status_code=401, detail="Registration token expired")

    runner_name = body.get("name", body.get("agentName", "runner"))
    labels_raw = body.get("labels", [])
    if isinstance(labels_raw, list):
        labels = [lbl.get("name", str(lbl)) if isinstance(lbl, dict) else str(lbl) for lbl in labels_raw]
    else:
        labels = ["self-hosted"]

    runner_token = f"ghp_runner_{secrets.token_urlsafe(32)}"
    token_hash = hash_token(runner_token)

    runner = Runner(
        name=runner_name,
        os=body.get("os", "linux"),
        status="online",
        labels=labels,
        busy=False,
        token_hash=token_hash,
        repo_id=repository.id,
        last_heartbeat=datetime.now(timezone.utc),
    )
    db.add(runner)
    await db.delete(reg)
    await db.commit()
    await db.refresh(runner)

    base = _request_base(request)
    return {
        "id": runner.id,
        "name": runner.name,
        "token": runner_token,
        "serverUrl": base,
        "gitServerUrl": base,
        "pipelines_url": f"{base}/_services/pipelines",
        "actionsServiceUrl": f"{base}/_apis/distributedtask",
    }


@router.delete("/_services/pipelines/{owner}/{repo}/_apis/pipelines/runs/{runner_id}")
async def pipelines_deregister_runner(
    owner: str, repo: str, runner_id: int, db: DbSession,
):
    """Deregister a runner via GHES pipelines protocol."""
    result = await db.execute(
        select(Runner).where(Runner.id == runner_id)
    )
    runner = result.scalar_one_or_none()
    if runner is None:
        raise HTTPException(status_code=404, detail="Runner not found")
    await db.delete(runner)
    await db.commit()
    return {"status": "removed"}


@router.get("/_services/pipelines/{owner}/{repo}/_apis/pipelines/runs")
async def pipelines_list_runners(
    owner: str, repo: str, db: DbSession,
):
    """List runners via GHES pipelines protocol."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Runner).where(Runner.repo_id == repository.id)
    )
    runners = result.scalars().all()
    return {
        "count": len(runners),
        "value": [
            {
                "id": r.id,
                "name": r.name,
                "status": r.status,
                "os": r.os,
            }
            for r in runners
        ],
    }
