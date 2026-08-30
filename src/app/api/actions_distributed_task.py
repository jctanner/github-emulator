"""GHES-internal distributed task endpoints for real actions/runner binary.

The real runner uses /_apis/distributedtask/ paths for session management
and job dispatch via long-poll. These endpoints implement the Azure Pipelines
agent protocol that the runner binary expects.
"""

import asyncio
import base64
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.api.deps import DbSession
from app.config import settings
from app.models.actions import (
    EnterpriseRunnerRegistrationToken,
    RegistrationToken,
    Runner,
    RunnerSession,
    WorkflowJob,
    WorkflowRun,
)
from app.models.repository import Repository
from app.services.auth_service import hash_token
from app.services.job_token_service import issue_job_token
from app.services.workflow_service import check_run_completion, dispatch_ready_jobs

router = APIRouter(tags=["actions-distributed-task"])


def _job_log_path(job_id: int) -> str:
    return os.path.join(settings.DATA_DIR, "logs", "jobs", f"{job_id}.log")


def _result_to_conclusion(result: str) -> str:
    if isinstance(result, int):
        return {
            0: "success",
            1: "success",
            2: "failure",
            3: "cancelled",
            4: "skipped",
            5: "cancelled",
        }.get(result, "failure")
    result = str(result)
    return {
        "Succeeded": "success",
        "SucceededWithIssues": "success",
        "Failed": "failure",
        "Cancelled": "cancelled",
        "Canceled": "cancelled",
        "Skipped": "skipped",
        "Abandoned": "cancelled",
    }.get(result, {
        "succeeded": "success",
        "succeededwithissues": "success",
        "failed": "failure",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "skipped": "skipped",
        "abandoned": "cancelled",
    }.get(result.lower(), "failure"))


def _is_success_result(result: str | int) -> bool:
    return _result_to_conclusion(result) == "success"


def _state_to_status(state: str) -> str:
    return {
        "Completed": "completed",
        "InProgress": "in_progress",
        "Pending": "queued",
    }.get(state, state)


def _is_expired(value: datetime) -> bool:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value < datetime.now(timezone.utc)


def _labels_from_body(body: dict) -> list[str]:
    labels_raw = body.get("labels", [])
    if isinstance(labels_raw, list):
        labels = []
        for label in labels_raw:
            if isinstance(label, dict):
                labels.append(str(label.get("name", label.get("Name", ""))))
            else:
                labels.append(str(label))
        return [label for label in labels if label]
    return ["self-hosted"]


def _request_base(request: Request) -> str:
    # The k3s proxy may replace Host with the internal service name. The
    # configured base URL is the externally reachable emulator URL and is
    # therefore the stable value to hand to runner clients.
    return settings.BASE_URL or f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"


def _runner_reachable_base_url(base_url: str | None = None) -> str:
    """URL embedded in job messages, consumed from inside the runner container."""
    parsed = urlsplit(base_url or settings.BASE_URL)
    if parsed.hostname == "ghemu.local" and parsed.port == 8000:
        netloc = parsed.hostname
        if parsed.username or parsed.password:
            auth = parsed.username or ""
            if parsed.password:
                auth = f"{auth}:{parsed.password}"
            netloc = f"{auth}@{netloc}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    return settings.BASE_URL


from app.api.actions_runner_protocol import (
    EMULATOR_INSTANCE_ID,
    EMULATOR_USER_ID,
    TASK_AREA_ID,
    _SERVICE_OWNER,
    _agent_json,
    _base64url_json,
    _context_dictionary,
    _context_value,
    _job_access_token,
    _runner_client_id,
    _service_definition,
    _template_mapping,
    _variable,
    _workflow_guid,
)

@router.get("/_apis/connectionData")
@router.get("/{owner}/{repo}/_apis/connectionData")
async def connection_data(request: Request):
    """Minimal anonymous connection data endpoint used by runner service clients."""
    request_base = _request_base(request)
    access_mapping = {
        "moniker": "PublicAccessMapping",
        "Moniker": "PublicAccessMapping",
        "displayName": "Public",
        "DisplayName": "Public",
        "accessPoint": request_base,
        "AccessPoint": request_base,
        "serviceOwner": TASK_AREA_ID,
        "ServiceOwner": TASK_AREA_ID,
    }
    location_service_data = {
        "ServiceOwner": TASK_AREA_ID,
        "AccessMappings": [access_mapping],
        "ClientCacheFresh": False,
        "ClientCacheTimeToLive": 3600,
        "DefaultAccessMappingMoniker": "PublicAccessMapping",
        "LastChangeId": 1,
        "LastChangeId64": 1,
        "ServiceDefinitions": [
            _service_definition(
                "a8c47e17-4d56-4a56-92bb-de7ea7dc65be",
                "pools",
                "_apis/distributedtask/pools",
            ),
            _service_definition(
                "e298ef32-5878-4cab-993c-043836571f42",
                "agents",
                "_apis/distributedtask/pools/{poolId}/agents/{agentId}",
            ),
            _service_definition(
                "134e239e-2df3-4794-a6f6-24f1f19ec8dc",
                "sessions",
                "_apis/distributedtask/pools/{poolId}/sessions/{sessionId}",
            ),
            _service_definition(
                "c3a054f6-7a8a-49c0-944e-3a8e5d7adfd7",
                "messages",
                "_apis/distributedtask/pools/{poolId}/messages/{messageId}",
            ),
            _service_definition(
                "fc825784-c92a-4299-9221-998a02d1b54f",
                "jobrequests",
                "_apis/distributedtask/pools/{poolId}/jobrequests/{requestId}",
            ),
            _service_definition(
                "858983e4-19bd-4c5e-864c-507b59b58b12",
                "timeline record feed",
                "_apis/distributedtask/hubs/{hubName}/plans/{planId}/timelines/{timelineId}/records/{recordId}/feed",
            ),
            _service_definition(
                "46f5667d-263a-4684-91b1-dff7fdcf64e2",
                "logs",
                "_apis/distributedtask/hubs/{hubName}/plans/{planId}/logs",
            ),
            _service_definition(
                "8893bc5b-35b2-4be7-83cb-99e683551db4",
                "timeline records",
                "_apis/distributedtask/hubs/{hubName}/plans/{planId}/timelines/{timelineId}/records",
            ),
        ],
    }
    location_service_data.update(
        {
            "serviceOwner": location_service_data["ServiceOwner"],
            "accessMappings": location_service_data["AccessMappings"],
            "clientCacheFresh": location_service_data["ClientCacheFresh"],
            "clientCacheTimeToLive": location_service_data["ClientCacheTimeToLive"],
            "defaultAccessMappingMoniker": location_service_data[
                "DefaultAccessMappingMoniker"
            ],
            "lastChangeId": location_service_data["LastChangeId"],
            "lastChangeId64": location_service_data["LastChangeId64"],
            "serviceDefinitions": location_service_data["ServiceDefinitions"],
        }
    )
    return {
        "authenticatedUser": {
            "id": EMULATOR_USER_ID,
            "providerDisplayName": "github-emulator",
            "customDisplayName": "github-emulator",
        },
        "authorizedUser": {
            "id": EMULATOR_USER_ID,
            "providerDisplayName": "github-emulator",
            "customDisplayName": "github-emulator",
        },
        "deploymentId": EMULATOR_INSTANCE_ID,
        "deploymentType": "Hosted",
        "instanceId": EMULATOR_INSTANCE_ID,
        "locationServiceData": location_service_data,
        "LocationServiceData": location_service_data,
    }


@router.options("/_apis/")
@router.options("/{owner}/{repo}/_apis/")
async def apis_root_options():
    """Minimal service root OPTIONS response for Azure DevOps-style clients."""
    return Response(
        status_code=200,
        headers={
            "Allow": "GET,POST,PATCH,DELETE,OPTIONS",
            "Public": "GET,POST,PATCH,DELETE,OPTIONS",
        },
    )


@router.post("/_apis/oauth2/token")
@router.post("/{owner}/{repo}/_apis/oauth2/token")
async def dt_oauth_token(request: Request, db: DbSession, runner_id: int = Query(...)):
    """Issue a short-lived bearer token for the runner listener."""
    raw = await request.body()
    form = parse_qs(raw.decode()) if raw else {}
    grant_type = form.get("grant_type", [""])[-1]
    if grant_type and grant_type != "client_credentials":
        raise HTTPException(status_code=400, detail="Unsupported grant_type")

    result = await db.execute(select(Runner).where(Runner.id == runner_id))
    runner = result.scalar_one_or_none()
    if runner is None:
        raise HTTPException(status_code=404, detail="Runner not found")

    access_token = f"ghp_runner_{secrets.token_urlsafe(32)}"
    runner.token_hash = hash_token(access_token)
    runner.status = "online"
    runner.last_heartbeat = datetime.now(timezone.utc)
    await db.commit()
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": 3600,
    }


async def _get_runner_from_token(request: Request, db) -> Runner:
    """Authenticate a runner from Authorization header."""
    auth = request.headers.get("Authorization", "")
    challenge = {"WWW-Authenticate": "Bearer"}
    token = ""
    if auth.startswith("Bearer "):
        token = auth[7:]
    elif auth.startswith("token "):
        token = auth[6:]
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers=challenge,
        )
    token_hash = hash_token(token)
    result = await db.execute(
        select(Runner).where(Runner.token_hash == token_hash)
    )
    runner = result.scalar_one_or_none()
    if runner is None:
        raise HTTPException(status_code=401, detail="Invalid token", headers=challenge)
    return runner


async def _get_job_from_job_token(request: Request, db) -> WorkflowJob:
    """Authenticate job-server calls that use the per-job endpoint token."""
    auth = request.headers.get("Authorization", "")
    challenge = {"WWW-Authenticate": "Bearer"}
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers=challenge,
        )

    token = auth[7:]
    try:
        payload_part = token.split(".")[1]
        payload_part += "=" * (-len(payload_part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_part.encode("ascii")))
    except (IndexError, ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="Invalid job token", headers=challenge) from None

    subject = str(payload.get("sub", ""))
    if not subject.startswith("job:"):
        raise HTTPException(status_code=401, detail="Invalid job token", headers=challenge)
    try:
        job_id = int(subject.split(":", 1)[1])
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid job token", headers=challenge) from None

    result = await db.execute(select(WorkflowJob).where(WorkflowJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=401, detail="Job token target not found", headers=challenge)
    return job


@router.post("/_apis/distributedtask/pools/{pool_id}/agents")
@router.post("/{owner}/{repo}/_apis/distributedtask/pools/{pool_id}/agents")
async def dt_register_pool_agent(
    pool_id: int, request: Request, db: DbSession,
):
    """Register an agent through the pool-scoped distributed-task endpoint."""
    body = await request.json()
    reg_token = body.get("token") or body.get("registrationToken")
    if not reg_token:
        runner = await _get_runner_from_token(request, db)
        runner.name = body.get("name", body.get("Name", runner.name))
        runner.os = body.get(
            "osDescription", body.get("OSDescription", body.get("os", runner.os))
        )
        runner.labels = _labels_from_body(body) or runner.labels
        runner.status = "online"
        runner.last_heartbeat = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(runner)
        return _agent_json(runner, base=_request_base(request))

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

    runner_token = f"ghp_runner_{secrets.token_urlsafe(32)}"
    runner = Runner(
        name=body.get("name", body.get("Name", body.get("agentName", "runner"))),
        os=body.get(
            "os", body.get("osDescription", body.get("OSDescription", "linux"))
        ),
        status="online",
        labels=_labels_from_body(body),
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
    return _agent_json(runner, runner_token, _request_base(request))


@router.get("/_apis/distributedtask/pools/{pool_id}/agents")
@router.get("/{owner}/{repo}/_apis/distributedtask/pools/{pool_id}/agents")
async def dt_list_pool_agents(
    pool_id: int,
    request: Request,
    db: DbSession,
    agentName: str | None = Query(None),
):
    """List registered pool agents."""
    await _get_runner_from_token(request, db)
    query = select(Runner).order_by(Runner.id)
    if agentName:
        query = query.where(Runner.name == agentName)
    result = await db.execute(query)
    runners = result.scalars().all()
    base = _request_base(request)
    return {"count": len(runners), "value": [_agent_json(runner, base=base) for runner in runners]}


@router.patch("/_apis/distributedtask/pools/{pool_id}/agents/{agent_id}")
@router.put("/_apis/distributedtask/pools/{pool_id}/agents/{agent_id}")
@router.patch("/{owner}/{repo}/_apis/distributedtask/pools/{pool_id}/agents/{agent_id}")
@router.put("/{owner}/{repo}/_apis/distributedtask/pools/{pool_id}/agents/{agent_id}")
async def dt_update_pool_agent(
    pool_id: int, agent_id: int, request: Request, db: DbSession,
):
    """Update an existing pool-scoped agent during runner replacement."""
    authenticated_runner = await _get_runner_from_token(request, db)
    body = await request.json()
    result = await db.execute(select(Runner).where(Runner.id == agent_id))
    runner = result.scalar_one_or_none()
    if runner is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    runner.name = body.get("name", body.get("Name", runner.name))
    runner.os = body.get(
        "osDescription", body.get("OSDescription", body.get("os", runner.os))
    )
    runner.labels = _labels_from_body(body) or runner.labels
    runner.status = "online"
    runner.last_heartbeat = datetime.now(timezone.utc)
    if authenticated_runner.id != runner.id:
        # config.sh --replace first creates a short-lived broker identity, then
        # updates the existing named runner. GitHub does not expose that broker
        # as a second runner registration.
        await db.delete(authenticated_runner)
    await db.commit()
    await db.refresh(runner)
    return _agent_json(runner, base=_request_base(request))


@router.get("/_apis/distributedtask/pools")
@router.get("/{owner}/{repo}/_apis/distributedtask/pools")
async def dt_list_pools(request: Request, db: DbSession):
    """List the default self-hosted runner pool."""
    await _get_runner_from_token(request, db)
    pools = [
        {
            "id": 1,
            "Id": 1,
            "name": "Default",
            "Name": "Default",
            "poolType": "automation",
            "PoolType": "automation",
            "isHosted": False,
            "IsHosted": False,
            "isInternal": True,
            "IsInternal": True,
            "size": 1,
            "Size": 1,
            "scope": EMULATOR_INSTANCE_ID,
            "Scope": EMULATOR_INSTANCE_ID,
        }
    ]
    return {"count": len(pools), "value": pools}


@router.delete("/_apis/distributedtask/pools/{pool_id}/agents/{agent_id}")
@router.delete("/{owner}/{repo}/_apis/distributedtask/pools/{pool_id}/agents/{agent_id}")
async def dt_delete_pool_agent(
    pool_id: int, agent_id: int, request: Request, db: DbSession,
):
    """Remove a pool-scoped agent."""
    await _get_runner_from_token(request, db)
    result = await db.execute(select(Runner).where(Runner.id == agent_id))
    runner = result.scalar_one_or_none()
    if runner is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    await db.delete(runner)
    await db.commit()
    return {"status": "removed"}


def _job_request_message(
    job: WorkflowJob,
    run: WorkflowRun | None,
    repo: Repository | None = None,
    base_url: str | None = None,
) -> dict:
    timeline_id = _workflow_guid("timeline", job.id)
    job_guid = _workflow_guid("job", job.id)
    plan_guid = _workflow_guid("run", job.run_id)
    runner_base_url = _runner_reachable_base_url(base_url)
    access_token = _job_access_token(job)
    repo_full_name = repo.full_name if repo else "unknown/unknown"
    repo_owner = repo_full_name.split("/", 1)[0]
    ref = (
        (run.trigger_payload or {}).get("ref")
        if run and isinstance(run.trigger_payload, dict)
        else None
    )
    ref = ref or f"refs/heads/{run.head_branch if run else 'main'}"
    sha = run.head_sha if run else "0" * 40
    workflow_name = job.workflow_name or job.name
    body = {
        "messageType": "PipelineAgentJobRequest",
        "jobId": job_guid,
        "jobName": job.name,
        "jobDisplayName": job.name,
        "requestId": job.id,
        "plan": {
            "planId": plan_guid,
            "planType": "Build",
            "version": 1,
        },
        "timeline": {
            "id": timeline_id,
        },
        "steps": [_job_step_message(job, step) for step in (job.steps or [])],
        "variables": {
            "system.github.token": _variable(access_token, is_secret=True),
            "system.github.job": _variable(job.name),
            "system.github.repository": _variable(repo_full_name),
            "system.github.repository_owner": _variable(repo_owner),
            "system.github.ref": _variable(ref),
            "system.github.sha": _variable(sha),
            "system.github.workflow": _variable(workflow_name),
        },
        "contextData": {
            "github": _context_dictionary({
                "api_url": f"{runner_base_url}/api/v3",
                "base_ref": "",
                "event_name": run.event if run else "workflow_dispatch",
                "event_path": "",
                "event": run.trigger_payload if run and run.trigger_payload else {},
                "graphql_url": f"{runner_base_url}/api/graphql",
                "head_ref": "",
                "job": job.name,
                "ref": ref,
                "repository": repo_full_name,
                "repository_id": str(repo.id) if repo else "0",
                "repository_owner": repo_owner,
                "repository_owner_id": str(repo.owner_id) if repo else "0",
                "retention_days": "90",
                "run_attempt": str(run.run_attempt if run else job.run_attempt),
                "run_id": str(run.id if run else job.run_id),
                "run_number": str(run.run_number if run else job.run_id),
                "server_url": runner_base_url,
                "sha": sha,
                "token": access_token,
                "workflow": workflow_name,
                "workspace": "",
            }),
        },
        "resources": {
            "endpoints": [
                {
                    "id": _workflow_guid("endpoint", job.id),
                    "name": "SystemVssConnection",
                    "type": "System",
                    "url": runner_base_url,
                    "authorization": {
                        "scheme": "OAuth",
                        "parameters": {
                            "AccessToken": access_token,
                        },
                    },
                    "isReady": True,
                }
            ],
            "repositories": [{
                "alias": "self",
                "properties": {
                    "id": str(run.repo_id) if run else "0",
                    "type": "git",
                    "url": runner_base_url,
                    "version": run.head_sha if run else "0" * 40,
                },
            }],
        },
    }
    encoded_body = json.dumps(body, separators=(",", ":"))
    return {
        "messageId": job.id,
        "messageType": "PipelineAgentJobRequest",
        "body": encoded_body,
        "MessageId": job.id,
        "MessageType": "PipelineAgentJobRequest",
        "Body": encoded_body,
    }


def _job_step_message(job: WorkflowJob, step: dict) -> dict:
    number = int(step.get("number", 0) or 0)
    display_name = step.get("name", f"Step {number}")
    inputs = {
        "script": step.get("run", ""),
        "shell": step.get("shell"),
        "workingDirectory": step.get("working-directory"),
    }
    return {
        "type": "Action",
        "id": _workflow_guid("step", (job.id * 1000) + number),
        "name": step.get("id") or f"step_{number}",
        "contextName": step.get("id"),
        "displayName": display_name,
        "enabled": True,
        "condition": step.get("if") or "success()",
        "environment": _template_mapping(step.get("env") or {}),
        "reference": {
            "type": "Script",
        },
        "inputs": _template_mapping(inputs),
    }


def _job_request_response(job: WorkflowJob, runner: Runner, result: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    locked_until = now.replace(microsecond=0) + timedelta(minutes=5)
    plan_guid = _workflow_guid("run", job.run_id)
    job_guid = _workflow_guid("job", job.id)
    response = {
        "requestId": job.id,
        "queueTime": job.created_at.isoformat() if job.created_at else now.isoformat(),
        "assignTime": job.started_at.isoformat() if job.started_at else now.isoformat(),
        "lockedUntil": locked_until.isoformat().replace("+00:00", "Z"),
        "serviceOwner": _SERVICE_OWNER,
        "hostId": _SERVICE_OWNER,
        "scopeId": _SERVICE_OWNER,
        "planType": "Build",
        "planId": plan_guid,
        "jobId": job_guid,
        "jobName": job.name,
        "lockToken": "00000000-0000-0000-0000-000000000000",
        "poolId": 1,
        "reservedAgent": {
            "id": runner.id,
            "name": runner.name,
            "version": "2.317.0",
            "osDescription": runner.os,
        },
    }
    if result:
        response["result"] = result
        response["finishTime"] = now.isoformat()
    return response


async def _claim_next_job(
    runner: Runner,
    db,
    base_url: str | None = None,
) -> dict | Response:
    # The upstream runner keeps polling for control messages while it is busy.
    # Those polls must not reserve another PipelineAgentJobRequest: the runner
    # acknowledges such a message but cannot execute it alongside its current
    # job, leaving the second job permanently in progress.
    if runner.busy or runner.status == "busy":
        return Response(status_code=204)

    job_query = (
        select(WorkflowJob)
        .join(WorkflowRun, WorkflowJob.run_id == WorkflowRun.id)
        .where(
            WorkflowJob.status == "queued",
            WorkflowJob.runner_id.is_(None),
        )
        .order_by(WorkflowJob.created_at)
    )
    if runner.repo_id is not None:
        job_query = job_query.where(WorkflowRun.repo_id == runner.repo_id)
    job_result = await db.execute(job_query)
    runner_labels = {str(label).lower() for label in (runner.labels or [])}
    job = next(
        (
            candidate
            for candidate in job_result.scalars().all()
            if {
                str(label).lower()
                for label in (candidate.labels or ["self-hosted"])
            }.issubset(runner_labels)
        ),
        None,
    )
    if job is None:
        return Response(status_code=204)

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
    if run and run.status == "queued":
        run.status = "in_progress"
        await db.commit()

    repo = None
    if run:
        repo_result = await db.execute(select(Repository).where(Repository.id == run.repo_id))
        repo = repo_result.scalar_one_or_none()

    return _job_request_message(job, run, repo, base_url)


async def _update_timeline_for_job(job: WorkflowJob, body: dict, db) -> None:
    records = body.get("value", body.get("records", []))
    if not records or not job.steps:
        return

    steps = list(job.steps)
    for record in records:
        order = record.get("order")
        if order is None and record.get("identifier"):
            try:
                order = int(record["identifier"])
            except (TypeError, ValueError):
                order = None
        record_id = str(record.get("id", record.get("Id", "")))
        record_name = str(
            record.get("name")
            or record.get("Name")
            or record.get("displayName")
            or record.get("DisplayName")
            or ""
        )
        for step in steps:
            step_number = int(step.get("number", 0) or 0)
            step_guid = _workflow_guid("step", (job.id * 1000) + step_number)
            step_name = str(step.get("name", ""))
            if (
                step_number == order
                or (record_id and record_id == step_guid)
                or (record_name and record_name == step_name)
            ):
                if "state" in record:
                    step["status"] = _state_to_status(record["state"])
                if "result" in record:
                    step["conclusion"] = _result_to_conclusion(record["result"])
                break
    job.steps = steps
    flag_modified(job, "steps")
    await db.flush()


async def _job_from_timeline_id(timeline_id: str, db) -> WorkflowJob | None:
    try:
        job_id = int(timeline_id)
    except ValueError:
        job_id = None
    if job_id is not None:
        result = await db.execute(select(WorkflowJob).where(WorkflowJob.id == job_id))
        return result.scalar_one_or_none()

    result = await db.execute(
        select(WorkflowJob).where(WorkflowJob.status == "in_progress")
    )
    for job in result.scalars().all():
        if _workflow_guid("timeline", job.id) == timeline_id:
            return job
    return None


async def _job_from_plan_id(plan_id: str, db) -> WorkflowJob | None:
    result = await db.execute(
        select(WorkflowJob)
        .where(WorkflowJob.status == "in_progress")
        .order_by(WorkflowJob.started_at.desc())
    )
    in_progress_jobs = result.scalars().all()
    for job in in_progress_jobs:
        if _workflow_guid("run", job.run_id) == plan_id:
            return job

    result = await db.execute(
        select(WorkflowJob)
        .order_by(WorkflowJob.created_at.desc())
        .limit(25)
    )
    for job in result.scalars().all():
        if _workflow_guid("run", job.run_id) == plan_id:
            return job
    return None


def _complete_queued_steps(job: WorkflowJob, result_str: str | int) -> None:
    if not job.steps:
        return
    conclusion = _result_to_conclusion(result_str)
    steps = list(job.steps)
    for step in steps:
        if step.get("status") != "completed":
            step["status"] = "completed"
            step["conclusion"] = conclusion if conclusion != "cancelled" else "skipped"
    job.steps = steps
    flag_modified(job, "steps")


async def _complete_job(job: WorkflowJob, runner: Runner, result_str: str, db) -> None:
    job.status = "completed"
    job.conclusion = _result_to_conclusion(result_str)
    job.completed_at = datetime.now(timezone.utc)
    if _is_success_result(result_str):
        _complete_queued_steps(job, result_str)

    runner.busy = False
    runner.status = "online"
    await db.flush()

    await dispatch_ready_jobs(db, job.run_id)
    await check_run_completion(db, job.run_id)
    await db.flush()


@router.post("/_apis/distributedtask/connect")
@router.post("/{owner}/{repo}/_apis/distributedtask/connect")
async def dt_connect(request: Request, db: DbSession):
    """Session negotiation. Runner opens a long-lived session."""
    runner = await _get_runner_from_token(request, db)

    session_id = str(uuid.uuid4())
    session = RunnerSession(
        runner_id=runner.id,
        session_id=session_id,
        last_seen=datetime.now(timezone.utc),
    )
    db.add(session)

    runner.status = "online"
    runner.last_heartbeat = datetime.now(timezone.utc)
    await db.commit()

    base = _request_base(request)
    return {
        "sessionId": session_id,
        "ownerName": "github-emulator",
        "serviceUrls": {
            "messageQueueUrl": f"{base}/_apis/distributedtask",
            "jobDispatchUrl": f"{base}/_apis/distributedtask",
            "blobStoreUrl": f"{base}/_apis/distributedtask/blobs",
        },
    }


@router.get("/_apis/distributedtask/session/{session_id}/messages")
@router.get("/{owner}/{repo}/_apis/distributedtask/session/{session_id}/messages")
async def dt_get_messages(
    session_id: str, request: Request, db: DbSession,
):
    """Long-poll for job messages. Returns a PipelineAgentJobRequest when available."""
    runner = await _get_runner_from_token(request, db)

    result = await db.execute(
        select(RunnerSession).where(RunnerSession.session_id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    session.last_seen = datetime.now(timezone.utc)
    runner.last_heartbeat = session.last_seen
    await db.commit()

    deadline = asyncio.get_event_loop().time() + 30
    while asyncio.get_event_loop().time() < deadline:
        message = await _claim_next_job(runner, db, _request_base(request))
        if not isinstance(message, Response):
            return message

        await asyncio.sleep(2)

    return Response(status_code=204)


@router.delete("/_apis/distributedtask/session/{session_id}")
@router.delete("/{owner}/{repo}/_apis/distributedtask/session/{session_id}")
async def dt_delete_session(
    session_id: str, request: Request, db: DbSession,
):
    """Close a runner session."""
    result = await db.execute(
        select(RunnerSession).where(RunnerSession.session_id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    runner_result = await db.execute(
        select(Runner).where(Runner.id == session.runner_id)
    )
    runner = runner_result.scalar_one_or_none()
    if runner:
        runner.status = "offline"
        runner.busy = False

    await db.delete(session)
    await db.commit()
    return {"status": "deleted"}


@router.post("/_apis/distributedtask/jobs/{job_id}/timeline")
@router.post("/{owner}/{repo}/_apis/distributedtask/jobs/{job_id}/timeline")
async def dt_update_timeline(
    job_id: int, request: Request, db: DbSession,
):
    """Runner reports step timeline updates."""
    runner = await _get_runner_from_token(request, db)
    body = await request.json()

    result = await db.execute(
        select(WorkflowJob).where(WorkflowJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    await _update_timeline_for_job(job, body, db)

    await db.commit()
    return {"status": "ok"}


@router.post("/_apis/distributedtask/jobs/{job_id}/complete")
@router.post("/{owner}/{repo}/_apis/distributedtask/jobs/{job_id}/complete")
async def dt_complete_job(
    job_id: int, request: Request, db: DbSession,
):
    """Runner reports job completion."""
    runner = await _get_runner_from_token(request, db)
    body = await request.json()

    result = await db.execute(
        select(WorkflowJob).where(WorkflowJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    await _complete_job(job, runner, body.get("result", "Succeeded"), db)
    await db.commit()

    return {"status": "completed"}


@router.post("/_apis/distributedtask/pools/{pool_id}/sessions")
@router.post("/{owner}/{repo}/_apis/distributedtask/pools/{pool_id}/sessions")
async def dt_create_pool_session(
    pool_id: int, request: Request, db: DbSession,
):
    """Pool-scoped session negotiation used by the Azure Pipelines runner protocol."""
    return await dt_connect(request, db)


@router.get("/_apis/distributedtask/pools/{pool_id}/sessions/{session_id}/messages")
@router.get("/{owner}/{repo}/_apis/distributedtask/pools/{pool_id}/sessions/{session_id}/messages")
async def dt_get_pool_messages(
    pool_id: int,
    session_id: str,
    request: Request,
    db: DbSession,
    lastMessageId: int = Query(0),
):
    """Pool-scoped long-poll for job messages."""
    return await dt_get_messages(session_id, request, db)


@router.get("/_apis/distributedtask/pools/{pool_id}/messages")
@router.get("/{owner}/{repo}/_apis/distributedtask/pools/{pool_id}/messages")
async def dt_get_pool_messages_by_query(
    pool_id: int,
    request: Request,
    db: DbSession,
    sessionId: str,
    lastMessageId: int = Query(0),
):
    """Generated-client message polling shape with sessionId in the query string."""
    return await dt_get_messages(sessionId, request, db)


@router.delete("/_apis/distributedtask/pools/{pool_id}/messages/{message_id}")
@router.delete("/{owner}/{repo}/_apis/distributedtask/pools/{pool_id}/messages/{message_id}")
async def dt_delete_pool_message(
    pool_id: int,
    message_id: int,
    request: Request,
    db: DbSession,
    sessionId: str | None = None,
):
    """Acknowledge removal of a delivered message from the queue."""
    await _get_runner_from_token(request, db)
    return {"messageId": message_id, "status": "deleted"}


@router.delete("/_apis/distributedtask/pools/{pool_id}/sessions/{session_id}")
@router.delete("/{owner}/{repo}/_apis/distributedtask/pools/{pool_id}/sessions/{session_id}")
async def dt_delete_pool_session(
    pool_id: int, session_id: str, request: Request, db: DbSession,
):
    """Pool-scoped session cleanup."""
    return await dt_delete_session(session_id, request, db)


@router.post("/_apis/distributedtask/pools/{pool_id}/jobrequests/{request_id}")
@router.post("/{owner}/{repo}/_apis/distributedtask/pools/{pool_id}/jobrequests/{request_id}")
async def dt_accept_pool_job_request(
    pool_id: int, request_id: int, request: Request, db: DbSession,
):
    """Acknowledge that the runner accepted a dispatched job request."""
    runner = await _get_runner_from_token(request, db)
    result = await db.execute(
        select(WorkflowJob).where(WorkflowJob.id == request_id, WorkflowJob.runner_id == runner.id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job request not found")
    return _job_request_response(job, runner)


@router.patch("/_apis/distributedtask/pools/{pool_id}/jobrequests/{request_id}")
@router.patch("/{owner}/{repo}/_apis/distributedtask/pools/{pool_id}/jobrequests/{request_id}")
async def dt_update_pool_job_request(
    pool_id: int, request_id: int, request: Request, db: DbSession,
):
    """Update or complete a pool-scoped job request."""
    runner = await _get_runner_from_token(request, db)
    body = await request.json()
    result = await db.execute(
        select(WorkflowJob).where(WorkflowJob.id == request_id, WorkflowJob.runner_id == runner.id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job request not found")

    query_result = request.query_params.get("result")
    if "result" in body and body["result"] is not None:
        await _complete_job(job, runner, body["result"], db)
    elif query_result is not None:
        await _complete_job(job, runner, query_result, db)
    elif str(body.get("state", "")).lower() == "completed":
        await _complete_job(job, runner, body.get("result", "Succeeded"), db)

    await db.commit()
    return _job_request_response(job, runner, body.get("result"))


@router.delete("/_apis/distributedtask/pools/{pool_id}/jobrequests/{request_id}")
@router.delete("/{owner}/{repo}/_apis/distributedtask/pools/{pool_id}/jobrequests/{request_id}")
async def dt_delete_pool_job_request(
    pool_id: int, request_id: int, request: Request, db: DbSession,
):
    """Complete a pool-scoped job request when the runner uses DELETE semantics."""
    runner = await _get_runner_from_token(request, db)
    result = await db.execute(
        select(WorkflowJob).where(WorkflowJob.id == request_id, WorkflowJob.runner_id == runner.id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job request not found")

    await _complete_job(job, runner, request.query_params.get("result", "Succeeded"), db)
    await db.commit()
    return Response(status_code=204)


@router.post("/_apis/distributedtask/pools/{pool_id}/timelines")
@router.post("/{owner}/{repo}/_apis/distributedtask/pools/{pool_id}/timelines")
async def dt_create_pool_timeline(pool_id: int, request: Request, db: DbSession):
    """Create a timeline placeholder for pool-scoped runner updates."""
    await _get_runner_from_token(request, db)
    return {"id": str(uuid.uuid4()), "records": []}


@router.patch("/_apis/distributedtask/pools/{pool_id}/timelines/{timeline_id}/records")
@router.patch("/{owner}/{repo}/_apis/distributedtask/pools/{pool_id}/timelines/{timeline_id}/records")
async def dt_update_pool_timeline_records(
    pool_id: int, timeline_id: str, request: Request, db: DbSession,
):
    """Update timeline records for a pool-scoped runner job."""
    await _get_runner_from_token(request, db)
    body = await request.json()
    job = await _job_from_timeline_id(timeline_id, db)
    if job is None:
        raise HTTPException(status_code=404, detail="Timeline not found")

    await _update_timeline_for_job(job, body, db)
    await db.commit()
    return {"id": timeline_id, "records": body.get("value", body.get("records", []))}


@router.post("/_apis/distributedtask/pools/{pool_id}/timelines/{timeline_id}/logs/{log_id}")
@router.post("/{owner}/{repo}/_apis/distributedtask/pools/{pool_id}/timelines/{timeline_id}/logs/{log_id}")
async def dt_upload_pool_timeline_log(
    pool_id: int, timeline_id: str, log_id: int, request: Request, db: DbSession,
):
    """Append log bytes for the job associated with a timeline."""
    await _get_runner_from_token(request, db)
    job = await _job_from_timeline_id(timeline_id, db)
    if job is None:
        raise HTTPException(status_code=404, detail="Timeline not found")

    log_data = await request.body()
    log_path = _job_log_path(job.id)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "ab") as f:
        f.write(log_data)

    return {"logId": log_id, "status": "ok"}


@router.post("/_apis/distributedtask/hubs/{hub_name}/plans/{plan_id}/timelines/{timeline_id}/records/{record_id}/feed")
@router.post("/{owner}/{repo}/_apis/distributedtask/hubs/{hub_name}/plans/{plan_id}/timelines/{timeline_id}/records/{record_id}/feed")
async def dt_append_timeline_record_feed(
    hub_name: str,
    plan_id: str,
    timeline_id: str,
    record_id: str,
    request: Request,
    db: DbSession,
):
    """Append web console lines emitted by the upstream runner worker."""
    auth_job = await _get_job_from_job_token(request, db)
    job = (
        await _job_from_plan_id(plan_id, db)
        or await _job_from_timeline_id(timeline_id, db)
        or auth_job
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    body = await request.json()
    lines = body.get("lines", body.get("Lines", body.get("value", body.get("Value", []))))
    if isinstance(lines, dict):
        lines = lines.get("value", lines.get("Value", []))
    if not lines:
        lines = [json.dumps(body, separators=(",", ":"))]
    log_path = _job_log_path(job.id)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "ab") as f:
        for line in lines:
            f.write(str(line).encode("utf-8") + b"\n")

    return Response(status_code=204)


@router.post("/_apis/distributedtask/hubs/{hub_name}/plans/{plan_id}/logs")
@router.post("/{owner}/{repo}/_apis/distributedtask/hubs/{hub_name}/plans/{plan_id}/logs")
async def dt_create_hub_plan_log(
    hub_name: str, plan_id: str, request: Request, db: DbSession,
):
    """Create a log placeholder or append raw log bytes from the upstream runner."""
    auth_job = await _get_job_from_job_token(request, db)
    content_type = request.headers.get("content-type", "")
    if "application/octet-stream" in content_type:
        job = await _job_from_plan_id(plan_id, db) or auth_job
        if job is None:
            raise HTTPException(status_code=404, detail="Plan not found")

        log_data = await request.body()
        log_path = _job_log_path(job.id)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "ab") as f:
            f.write(log_data)

        return {"id": 1, "lineCount": 0}

    body = await request.json()
    return {
        "id": body.get("id", body.get("Id", 1)),
        "lineCount": 0,
        "createdOn": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/_apis/distributedtask/hubs/{hub_name}/plans/{plan_id}/logs/{log_id}")
@router.post("/{owner}/{repo}/_apis/distributedtask/hubs/{hub_name}/plans/{plan_id}/logs/{log_id}")
async def dt_append_hub_plan_log(
    hub_name: str,
    plan_id: str,
    log_id: int,
    request: Request,
    db: DbSession,
):
    """Append uploaded log file bytes from the upstream runner worker."""
    auth_job = await _get_job_from_job_token(request, db)
    job = await _job_from_plan_id(plan_id, db) or auth_job
    if job is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    log_data = await request.body()
    log_path = _job_log_path(job.id)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "ab") as f:
        f.write(log_data)

    return {"id": log_id, "lineCount": 0}


@router.patch("/_apis/distributedtask/hubs/{hub_name}/plans/{plan_id}/timelines/{timeline_id}/records")
@router.patch("/{owner}/{repo}/_apis/distributedtask/hubs/{hub_name}/plans/{plan_id}/timelines/{timeline_id}/records")
async def dt_update_hub_timeline_records(
    hub_name: str,
    plan_id: str,
    timeline_id: str,
    request: Request,
    db: DbSession,
):
    """Update timeline records emitted by the upstream runner worker."""
    auth_job = await _get_job_from_job_token(request, db)
    body = await request.json()
    job = (
        await _job_from_plan_id(plan_id, db)
        or await _job_from_timeline_id(timeline_id, db)
        or auth_job
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Timeline not found")

    await _update_timeline_for_job(job, body, db)
    await db.commit()
    return body.get("value", body.get("records", []))
