"""GHES-internal distributed task endpoints for real actions/runner binary.

The real runner uses /_apis/distributedtask/ paths for session management
and job dispatch via long-poll. These endpoints implement the Azure Pipelines
agent protocol that the runner binary expects.
"""

import asyncio
import base64
import itertools
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.orm.attributes import flag_modified

from app.api.actions_dispatch import _claim_queued_job
from app.api.actions_runner_protocol import (
    _SERVICE_OWNER,
    EMULATOR_INSTANCE_ID,
    EMULATOR_USER_ID,
    TASK_AREA_ID,
    _agent_json,
    _context_dictionary,
    _job_access_token,
    _service_definition,
    _template_mapping,
    _variable,
    _workflow_guid,
)
from app.api.actions_runners import _delete_runner_and_detach_history, normalise_runner_os
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
from app.services.action_download_service import (
    UpstreamActionError,
    build_local_action_archive,
    fetch_action_archive,
    parse_uses,
    resolve_action_sha,
    resolve_local_action_sha,
)
from app.services.auth_service import hash_token
from app.services.job_token_service import validate_job_token
from app.services.pull_request_runs import is_fork_pull_request_run
from app.services.workflow_service import (
    _branch_protected,
    check_run_completion,
    dispatch_ready_jobs,
    static_github_facts,
)

router = APIRouter(tags=["actions-distributed-task"])
# Under github_emulator, which app.main sends to the server log: job
# hand-outs, requeues, completions and cancellations can be traced there.
logger = logging.getLogger("github_emulator.runner_broker")


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
    return base_url or settings.BASE_URL


def _request_base(request: Request) -> str:
    # The k3s proxy may replace Host with the internal service name. The
    # configured base URL is the externally reachable emulator URL and is
    # therefore the stable value to hand to runner clients. The local
    # upstream-runner harness proxies ghemu.local:80 to the host's :8000, so
    # normalize that one development URL before embedding service locations.
    configured = settings.BASE_URL or (
        f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"
    )
    return _runner_reachable_base_url(configured)


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
            _service_definition(
                "27d7f831-88c1-4719-8ca1-6a061dad90eb",
                "action download info",
                "_apis/distributedtask/hubs/{hubName}/plans/{planId}/actionsdownloadinfo",
            ),
            # TaskResourceIds.PlanEvents: the worker raises JobCompleted here,
            # and that event is the only place the job's outputs travel.
            _service_definition(
                "557624af-b29e-4c20-8ab0-0399d2204f3f",
                "events",
                "_apis/distributedtask/hubs/{hubName}/plans/{planId}/events",
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


async def _get_runner_from_token(request: Request, db, allow_remove_only: bool = False) -> Runner:
    """Authenticate a runner from Authorization header.

    A remove-only credential (config.sh remove's) is refused unless the route
    allows it: only deleting an agent does.
    """
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
    if runner is None or (runner.remove_only and not allow_remove_only):
        raise HTTPException(status_code=401, detail="Invalid token", headers=challenge)
    return runner


def _same_scope_as(runner: Runner):
    """Runners registered where ``runner`` was: the same repo, org or enterprise.

    On GitHub a runner belongs to the repository, organization or enterprise
    it registered with, and a runner credential sees only the agents there.
    """
    def eq(column, value):
        return column.is_(None) if value is None else column == value

    return and_(
        eq(Runner.repo_id, runner.repo_id),
        eq(Runner.org_id, runner.org_id),
        eq(Runner.enterprise_slug, runner.enterprise_slug),
    )


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
    return await _job_from_token_value(auth[7:], db, challenge)


async def _get_job_from_download_credential(request: Request, db) -> WorkflowJob:
    """The runner downloads an action archive with Basic `x-access-token:<token>`.

    That token is the job's own (ActionManager fills it from `github.token`
    when the download info names none), so the archive proxy authenticates
    the same way as every other job-server call.
    """
    auth = request.headers.get("Authorization", "")
    challenge = {"WWW-Authenticate": "Basic"}
    if auth.startswith("Bearer "):
        return await _job_from_token_value(auth[7:], db, challenge)
    if not auth.startswith("Basic "):
        raise HTTPException(status_code=401, detail="Authentication required", headers=challenge)
    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=401, detail="Invalid credential", headers=challenge) from None
    _user, _sep, token = decoded.partition(":")
    return await _job_from_token_value(token, db, challenge)


async def _job_from_token_value(token: str, db, challenge: dict) -> WorkflowJob:
    # Verify the signature, issuer and lifetime the token was issued with;
    # its claims are only as trustworthy as that check.
    validated = await validate_job_token(db, token)
    if validated is None:
        raise HTTPException(status_code=401, detail="Invalid job token", headers=challenge)
    job, _run = validated
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
        runner.os = normalise_runner_os(
            body.get("osDescription", body.get("OSDescription", body.get("os")))
            or runner.os
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
    if enterprise_reg is not None and enterprise_reg.for_remove:
        enterprise_reg = None  # a remove token registers no runner
    if reg is None and enterprise_reg is None:
        raise HTTPException(status_code=401, detail="Invalid registration token")
    registration = reg or enterprise_reg
    if _is_expired(registration.expires_at):
        raise HTTPException(status_code=401, detail="Registration token expired")

    runner_token = f"ghp_runner_{secrets.token_urlsafe(32)}"
    runner = Runner(
        name=body.get("name", body.get("Name", body.get("agentName", "runner"))),
        os=normalise_runner_os(
            body.get("os", body.get("osDescription", body.get("OSDescription")))
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
    # The registration token stays until it expires: on GitHub one token
    # registers any number of runners within its hour.
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
    """List registered pool agents in the caller's scope.

    config.sh remove looks its agent up here before deleting it, so the
    remove-only credential may list too. Remove-only credentials are never
    listed: they are not runners.
    """
    caller = await _get_runner_from_token(request, db, allow_remove_only=True)
    query = select(Runner).where(
        _same_scope_as(caller), Runner.remove_only.is_not(True),
    ).order_by(Runner.id)
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
    result = await db.execute(select(Runner).where(
        Runner.id == agent_id, _same_scope_as(authenticated_runner),
    ))
    runner = result.scalar_one_or_none()
    if runner is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    runner.name = body.get("name", body.get("Name", runner.name))
    runner.os = normalise_runner_os(
        body.get("osDescription", body.get("OSDescription", body.get("os")))
        or runner.os
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
    authenticated_runner = await _get_runner_from_token(request, db, allow_remove_only=True)
    result = await db.execute(select(Runner).where(
        Runner.id == agent_id, _same_scope_as(authenticated_runner),
    ))
    runner = result.scalar_one_or_none()
    if runner is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if authenticated_runner.remove_only and authenticated_runner.id != runner.id:
        # config.sh remove's credential is spent once the agent is gone.
        await db.delete(authenticated_runner)
    # Detach the jobs that ran on it (PostgreSQL enforces the foreign key);
    # a job it still runs fails, as the runner will not finish it.
    await _delete_runner_and_detach_history(db, runner, fail_live_jobs=True)
    return {"status": "removed"}


def _pull_request_branch(run: WorkflowRun | None, side: str) -> str:
    """github.base_ref / head_ref of a pull_request run: the pull request's
    base and head branches. Empty for every other event here."""
    if run is None or run.event != "pull_request":
        return ""
    pull_request = (run.trigger_payload or {}).get("pull_request") or {}
    return str((pull_request.get(side) or {}).get("ref") or "")


def _message_ref(run: WorkflowRun | None) -> str:
    """The full ref the job message names: the event's, else the run's branch."""
    ref = (
        (run.trigger_payload or {}).get("ref")
        if run and isinstance(run.trigger_payload, dict)
        else None
    )
    return ref or f"refs/heads/{run.head_branch if run else 'main'}"


def _job_request_message(
    job: WorkflowJob,
    run: WorkflowRun | None,
    repo: Repository | None = None,
    base_url: str | None = None,
    ref_protected: bool = False,
) -> dict:
    timeline_id = _workflow_guid("timeline", job.id)
    job_guid = _workflow_guid("job", job.id)
    plan_guid = _workflow_guid("run", job.run_id)
    runner_base_url = _runner_reachable_base_url(base_url)
    access_token = _job_access_token(job)
    repo_full_name = repo.full_name if repo else "unknown/unknown"
    repo_owner = repo_full_name.split("/", 1)[0]
    ref = _message_ref(run)
    sha = run.head_sha if run else "0" * 40
    workflow_name = job.workflow_name or job.name
    # Actions OIDC, as the upstream runner sees it. The runner's own plumbing
    # for these two variables was not reverse-engineered, so they are placed
    # directly in each step's environment, which is what actually has to hold
    # them when the step runs. A job that did not declare "id-token: write"
    # gets neither, matching GitHub.
    may_mint = (
        (job.permissions is None or job.permissions.get("id-token") == "write")
        and not is_fork_pull_request_run(run)
    )
    oidc_environment = (
        {
            "ACTIONS_ID_TOKEN_REQUEST_URL": (
                f"{runner_base_url}/actions/oidc/token?api-version=2.0"
            ),
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": access_token,
        }
        if may_mint
        else {}
    )
    # gh does not read GITHUB_API_URL. Without GH_HOST every gh call in a step
    # went to api.github.com with the emulator's token and failed with "Bad
    # credentials" (the Python runner sets it per step for the same reason).
    step_environment = {**oidc_environment, "GH_HOST": _gh_host(runner_base_url)}
    body = {
        "messageType": "PipelineAgentJobRequest",
        "jobId": job_guid,
        "jobName": job.name,
        "jobDisplayName": job.name,
        "requestId": job.id,
        "plan": {
            "planId": plan_guid,
            "planType": "Build",
            # From plan version 8 the worker raises JobCompleted, carrying the
            # job's outputs, and the listener no longer finishes the request
            # (PlanUtil.GetFeatures). Below 8 the outputs are never sent, so
            # dt_raise_plan_event completes the job instead.
            "version": 8,
        },
        "timeline": {
            "id": timeline_id,
        },
        "steps": [
            _job_step_message(job, step, step_environment)
            for step in (job.steps or [])
        ],
        # The job's `outputs:`, which the runner evaluates against its steps
        # when the job ends and reports in JobCompleted. Without them it has
        # nothing to report, and needs.<job>.outputs stays empty.
        **({"jobOutputs": _template_mapping(job.outputs_config)} if job.outputs_config else {}),
        "variables": {
            "system.github.token": _variable(access_token, is_secret=True),
            "system.github.job": _variable(job.name),
            "system.github.repository": _variable(repo_full_name),
            "system.github.repository_owner": _variable(repo_owner),
            "system.github.ref": _variable(ref),
            "system.github.sha": _variable(sha),
            "system.github.workflow": _variable(workflow_name),
            # actions/runner processes `::notice::` only when the server
            # turns this on (ActionCommandManager: "Enhanced Annotations not
            # enabled on the server: 'notice' command will not be
            # processed"); without it the line stays raw in the log. GitHub
            # documents notice as a workflow command beside warning and
            # error, rendered as `##[notice]` in the job log.
            "DistributedTask.EnhancedAnnotations": _variable("true"),
            # runner.environment and RUNNER_ENVIRONMENT. The jobs stand in for
            # GitHub-hosted ones, which is what workflows here are written for.
            "system.runnerEnvironment": _variable("github-hosted"),
        },
        "contextData": {
            # The runner expands an expression only when every context the
            # schema allows for that field is registered (TemplateEvaluator:
            # Expand = available.IsSupersetOf(allowed)). An action's input
            # default allows github, strategy, matrix, job, runner and
            # hashFiles; job and runner the worker adds itself, so without
            # these two `default: ${{ github.repository }}` in
            # actions/checkout stays an expression and the step fails with
            # "Unexpected type 'BasicExpressionToken'". GitHub sends both on
            # every job, matrix as null when there is none; matrix values
            # here are already rendered into the steps at job creation.
            "strategy": _context_dictionary({
                "fail-fast": True,
                "job-index": 0,
                "job-total": 1,
                "max-parallel": 1,
            }),
            "matrix": None,
            "github": _context_dictionary({
                "actor": run.actor.login if run and run.actor else "",
                "actor_id": str(run.actor_id) if run and run.actor_id else "",
                "api_url": f"{runner_base_url}/api/v3",
                "base_ref": _pull_request_branch(run, "base"),
                "event_name": run.event if run else "workflow_dispatch",
                "event_path": "",
                "event": run.trigger_payload if run and run.trigger_payload else {},
                "graphql_url": f"{runner_base_url}/api/graphql",
                "head_ref": _pull_request_branch(run, "head"),
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
                "triggering_actor": run.actor.login if run and run.actor else "",
                "workflow": workflow_name,
                "workflow_ref": (
                    f"{repo_full_name}/{run.workflow.path}@{ref}"
                    if run and run.workflow and run.workflow.path else ""
                ),
                "workspace": "",
                # ref_name, ref_type, ref_protected, workflow_sha and the
                # rest: the runner exports the first four as GITHUB_* env.
                **static_github_facts(
                    ref, sha, repo_full_name, repo.private if repo else None, ref_protected,
                ),
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
                    # The runner exports ResultsServiceUrl to node actions as
                    # ACTIONS_RESULTS_URL (NodeScriptActionHandler), where
                    # upload-artifact@v4+ and download-artifact@v4+ find the
                    # results service (src/app/api/actions_results.py).
                    "data": {
                        "ResultsServiceUrl": f"{runner_base_url}/",
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


def _job_step_message(
    job: WorkflowJob, step: dict, extra_environment: dict | None = None
) -> dict:
    number = int(step.get("number", 0) or 0)
    display_name = step.get("name", f"Step {number}")
    uses = step.get("uses")
    reference: dict = {"type": "Script"}
    if uses:
        parsed = parse_uses(uses)
        if parsed is None:
            # docker:// images and values the runner's own grammar rejects.
            # Fail where the step stands rather than as an empty script
            # that reports success.
            inputs = {
                "script": _unsupported_uses_script(uses),
                "shell": "bash",
                "workingDirectory": step.get("working-directory"),
            }
        else:
            # The shape PipelineTemplateConverter produces. The runner asks
            # for download info on the GitHub ones before the job starts and
            # takes "self" ones from the checked-out workspace.
            reference = parsed.as_step_reference()
            inputs = dict(step.get("with") or {})
    else:
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
        # The step's own env wins, so a workflow can still override these.
        "environment": _template_mapping(
            _with_gh_enterprise_token({**(extra_environment or {}), **(step.get("env") or {})})
        ),
        "reference": reference,
        "inputs": _template_mapping(inputs),
    }


def _gh_host(base_url: str) -> str:
    """The emulator's host as gh's GH_HOST takes it: no scheme, no default port."""
    parts = urlsplit(base_url)
    default = {"http": 80, "https": 443}.get(parts.scheme)
    if parts.port and parts.port != default:
        return f"{parts.hostname}:{parts.port}"
    return parts.hostname or ""


def _with_gh_enterprise_token(environment: dict) -> dict:
    """Mirror the token the workflow chose into gh's enterprise variables.

    gh treats any host but github.com as GitHub Enterprise and reads its
    credential from GH_ENTERPRISE_TOKEN, ignoring GH_TOKEN. Only a token the
    workflow declared is mirrored, as the same expression so the runner
    resolves it when the step starts: an ambient job token here would outrank
    a narrower token a step mints for itself (the Python runner learnt the
    same). A workflow that sets the enterprise variables itself wins.
    """
    token = environment.get("GH_TOKEN") or environment.get("GITHUB_TOKEN")
    if token:
        environment.setdefault("GH_ENTERPRISE_TOKEN", token)
        environment.setdefault("GITHUB_ENTERPRISE_TOKEN", token)
    return environment


def _unsupported_uses_script(uses: str) -> str:
    """A script that fails loudly in place of an action this path cannot run.

    The `::error::` line becomes an annotation on the run, so the refusal is
    visible from the UI and not only from the step log.
    """
    if uses.startswith("docker://"):
        why = ("container actions are not supported on this runner path; "
               "the action was NOT executed")
    else:
        why = ("expected {owner}/{repo}[/path]@ref or ./path, so the action "
               "was NOT executed")
    message = f"::error::uses: {uses} was not run: {why}"
    # One single-quoted word, so nothing in the action name or the reason
    # is read by the shell.
    quoted = "'" + message.replace("'", "'\\''") + "'"
    return f"echo {quoted} >&2\nexit 1"


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
    job = None
    for candidate in job_result.scalars().all():
        if not {
            str(label).lower()
            for label in (candidate.labels or ["self-hosted"])
        }.issubset(runner_labels):
            continue
        # Another runner's poll may have claimed this job since it was read.
        if await _claim_queued_job(db, candidate, runner):
            job = candidate
            break
    if job is None:
        return Response(status_code=204)

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

    # Looked up here, on the async session, rather than through
    # repo.branches: the message builder is synchronous and must not load a
    # relationship.
    ref_protected = await _branch_protected(db, run.repo_id if run else None, _message_ref(run))
    return _job_request_message(job, run, repo, base_url, ref_protected)


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


async def _job_for_plan(request: Request, db, plan_id: str, timeline_id: str | None = None) -> WorkflowJob:
    """The job token's own job, when the plan and timeline in the path are its own.

    The runner addresses each job-server call with the plan (the run) and
    timeline (the job) ids from its job message. A token reaches only those;
    any other plan or timeline is 404.
    """
    job = await _get_job_from_job_token(request, db)
    if plan_id != _workflow_guid("run", job.run_id):
        raise HTTPException(status_code=404, detail="Plan not found")
    if timeline_id is not None and timeline_id not in (_workflow_guid("timeline", job.id), str(job.id)):
        raise HTTPException(status_code=404, detail="Timeline not found")
    return job


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


# The job each runner was last handed: runner id -> (job id, job guid). Kept
# apart from the job row, because deleting a repository deletes its runs and
# jobs while runners may still be executing them.
_holding: dict[int, tuple[int, str]] = {}

# How long after handing out a job a runner's "Online" is not believed.
_IDLE_GRACE = timedelta(minutes=2)

# The real runner abandons its message poll whenever its status changes and
# opens a new one at once; the server keeps running the abandoned request. A
# job that request claimed was answered to nobody, and sat in progress on a
# runner that never saw it. So only a runner's newest poll may hand out a
# job, and hand-outs and the settling of a runner's state are serialised per
# runner.
_poll_generation: dict[int, int] = {}
# A poll's generation is its arrival number, taken before the poll's first
# await. Taken after the token lookup and the heartbeat commit, an older poll
# delayed there got a higher generation than a newer live one, and replaced
# it. One counter for every runner: only the order matters.
_poll_arrivals = itertools.count(1)
# The job message each runner last acknowledged (DELETE of the message, which
# the runner sends once it has dispatched the job, before its next poll): that
# job was received for certain, whatever lastMessageId a late poll carries.
# One entry per runner, replaced only by the runner's next acknowledgement,
# which follows a hand-out that needed the previous job's report committed.
# Dropping the entry when the report arrived opened a gap, before that report
# committed, in which the job looked unreceived and was requeued after it ran.
_acknowledged: dict[int, int] = {}
# The job whose JobCancellation each runner last acknowledged. Until then the
# cancellation is sent again on every live poll, so a response the runner
# abandoned or never got does not lose it. In memory: after a restart the
# runner may get it once more, and it ignores a cancellation for a job it no
# longer runs.
_cancellation_acknowledged: dict[int, int] = {}
_runner_locks: dict[tuple[int, int], asyncio.Lock] = {}

# JobCancellation message ids, apart from job message ids (the job id), so a
# DELETE of one is not taken for the other.
_CANCELLATION_MESSAGE_BASE = 1 << 40

# How long a handed-out job may stay unacknowledged before a poll that does
# not name it counts as proof the runner never received it.
_ACK_WINDOW = timedelta(seconds=5)

# Long-poll pacing; tests shorten them.
_POLL_SECONDS = 30.0
_POLL_INTERVAL_SECONDS = 2.0


def _runner_lock(runner_id: int) -> asyncio.Lock:
    # Keyed by event loop too: a lock is bound to the loop it first waits on.
    loop = id(asyncio.get_running_loop())
    key = (loop, runner_id)
    lock = _runner_locks.get(key)
    if lock is None:
        # Locks of a loop that has gone (tests run one loop each) are dead.
        for stale in [k for k, held in _runner_locks.items() if k[0] != loop and not held.locked()]:
            del _runner_locks[stale]
        lock = _runner_locks[key] = asyncio.Lock()
    return lock


def _reset_broker_state() -> None:
    """Forget every runner's in-memory broker state (tests; ids restart at 1)."""
    for state in (_holding, _poll_generation, _acknowledged, _cancellation_acknowledged, _runner_locks):
        state.clear()


async def _pending_cancellation(runner: Runner, db) -> dict | None:
    """A JobCancellation message for the job this runner holds, once the job
    was cancelled while it ran (a newer job superseding it under
    ``concurrency: cancel-in-progress``, or a run cancelled through the API),
    or no longer exists (its repository was deleted, as GitHub cancels a
    deleted repository's running jobs).

    Without it the runner never heard: the job was cancelled in the database
    and ran to its end anyway, so a superseded stage ran twice; or the job
    vanished and the runner stayed busy for good.

    Reads are fresh (``populate_existing``): the long poll keeps one session,
    which does not expire objects on commit, so a cancellation committed by
    another request mid-poll was otherwise hidden behind the job row loaded
    on the first pass.
    """
    held = _holding.get(runner.id)
    if held is not None:
        # Held means busy: the hold ends only where the runner is freed.
        job_id, job_guid = held
        job = (await db.execute(
            select(WorkflowJob)
            .where(WorkflowJob.id == job_id)
            .execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if job is not None and job.conclusion != "cancelled":
            return None
    else:
        # After a restart nothing is held in memory; ask the database.
        await db.refresh(runner, attribute_names=["busy", "status"])
        if not (runner.busy or runner.status == "busy"):
            return None
        job = (await db.execute(
            select(WorkflowJob)
            .where(WorkflowJob.runner_id == runner.id)
            .order_by(WorkflowJob.started_at.desc(), WorkflowJob.id.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if job is None or job.conclusion != "cancelled":
            return None
        job_id, job_guid = job.id, _workflow_guid("job", job.id)
    if _cancellation_acknowledged.get(runner.id) == job_id:
        return None
    # The shape the runner's JobCancelMessage reads; the timeout is how long
    # its steps get to stop before they are killed.
    body = json.dumps({"jobId": job_guid, "timeout": "00:05:00"}, separators=(",", ":"))
    message_id = _CANCELLATION_MESSAGE_BASE + job_id
    return {
        "messageId": message_id,
        "messageType": "JobCancellation",
        "body": body,
        "MessageId": message_id,
        "MessageType": "JobCancellation",
        "Body": body,
    }


def _age(started: datetime | None, now: datetime) -> timedelta:
    """How long ago a job was handed out; a job with no start time counts as old."""
    if started is None:
        return _IDLE_GRACE
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return now - started


def _job_started(job: WorkflowJob) -> bool:
    """Whether the runner reported any step of ``job`` (timeline updates)."""
    return any((step or {}).get("status") not in (None, "queued") for step in (job.steps or []))


async def _settle_idle_runner(runner: Runner, status: str, last_message_id: str, db) -> None:
    """Believe a runner that says it is idle.

    The runner reports its state (status=Online or Busy) and the id of the
    last message it received (lastMessageId) on every poll. A runner we hold
    busy that says Online either never received the job we handed it (the
    poll that carried it was abandoned, see ``_poll_generation``), or
    finished or lost it without a report reaching us (its job was deleted,
    or the report failed). Left busy, it was never given another job.

    A job it never received goes back to the queue. One it received and
    still holds after the grace period fails, so the run is not left waiting
    for good. Either way the runner is freed.
    """
    if status.strip().lower() != "online":
        return
    await db.refresh(runner, attribute_names=["busy", "status"])
    if not (runner.busy or runner.status == "busy"):
        return
    now = datetime.now(timezone.utc)
    held = _holding.get(runner.id)
    # The held job whose cancellation, should one commit before the runner is
    # freed, must still reach the runner.
    watched = None
    if held is not None and _cancellation_acknowledged.get(runner.id) != held[0]:
        watched = held[0]
        # The held job was cancelled and its cancellation is not yet
        # acknowledged. An Online poll the runner sent before it took the job
        # can still arrive; freeing the runner on it would drop the
        # cancellation while the job runs. Keep it busy within the grace
        # period, so the cancellation is sent.
        held_job = (await db.execute(
            select(WorkflowJob)
            .where(WorkflowJob.id == held[0])
            .execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if held_job is None or (
            held_job.conclusion == "cancelled"
            and _age(held_job.started_at, now) < _IDLE_GRACE
        ):
            # A deleted job (its repository was deleted) has no start time to
            # bound the wait; its cancellation goes out on this poll, and the
            # runner's acknowledgement lets the next Online poll free it.
            await db.commit()
            return
        if held_job.conclusion == "cancelled":
            # Past the grace period the runner is idle for good.
            watched = None
    jobs = (await db.execute(
        select(WorkflowJob)
        .where(WorkflowJob.runner_id == runner.id, WorkflowJob.status == "in_progress")
        .execution_options(populate_existing=True)
    )).scalars().all()
    for job in jobs:
        age = _age(job.started_at, now)
        # Never received: the runner names another message as its last, or
        # (a runner that has had no message since it connected) names none
        # and the job never started a step. A poll the runner gave up on
        # before the hand-out came back looks like that; failing the job
        # then lost a run the runner never saw.
        # A runner restarted mid-job also sends no last message, but its
        # job has started steps, so it still fails below.
        never_received = (
            (last_message_id and last_message_id != str(job.id))
            or (not last_message_id and not _job_started(job))
        )
        if never_received and _acknowledged.get(runner.id) != job.id:
            if age < _ACK_WINDOW:
                # A request the runner sent before it got the job can still
                # reach us after the hand-out; the acknowledgement follows
                # within milliseconds. Decide on a later pass.
                await db.commit()
                return
            # Conditional: a report of this job committed meanwhile wins.
            requeued = await db.execute(
                update(WorkflowJob)
                .where(
                    WorkflowJob.id == job.id,
                    WorkflowJob.status == "in_progress",
                    WorkflowJob.runner_id == runner.id,
                )
                .values(status="queued", runner_id=None, runner_name=None, started_at=None)
                .execution_options(synchronize_session=False)
            )
            if requeued.rowcount == 1:
                logger.info(
                    "Runner %s never received job %s (its last message is %s); job requeued",
                    runner.name, job.id, last_message_id or "none",
                )
            continue
        if age < _IDLE_GRACE:
            # The runner can poll once more as Online right after it took
            # the job, before its dispatcher marks it Busy.
            await db.commit()
            return
        logger.warning("Runner %s reports idle while holding job %s; failing the job", runner.name, job.id)
        _holding[runner.id] = (job.id, _workflow_guid("job", job.id))
        await _complete_job(job, runner, "Failed", db)
    if watched is not None and not await _free_runner_unless_cancelled(runner, watched, db):
        # The held job was cancelled (or deleted) after it was read above, so
        # the in-progress list missed it. Keep the hold: the cancellation goes
        # out on this poll.
        logger.info("Runner %s's job %s was cancelled while its poll settled; left busy", runner.name, watched)
        await db.commit()
        return
    _holding.pop(runner.id, None)
    runner.busy = False
    runner.status = "online"
    await db.commit()


async def _free_runner_unless_cancelled(runner: Runner, job_id: int, db) -> bool:
    """Mark ``runner`` idle unless its job ``job_id`` is cancelled or gone.

    One statement, so the job's conclusion is read where the runner is
    written: SQLite runs it under its write lock, and a cancellation commits
    either before it (the runner stays busy) or after it. The cancellation
    writers run inside other requests' transactions (run cancel, concurrency
    supersede, repository delete) and do not take the runner's lock.
    """
    await db.flush()
    freed = await db.execute(
        update(Runner)
        .where(
            Runner.id == runner.id,
            exists().where(
                WorkflowJob.id == job_id,
                or_(WorkflowJob.conclusion.is_(None), WorkflowJob.conclusion != "cancelled"),
            ),
        )
        .values(busy=False, status="online")
        .execution_options(synchronize_session=False)
    )
    await db.refresh(runner, attribute_names=["busy", "status"])
    return freed.rowcount == 1


async def _release_claim(runner: Runner, job_id: int, db) -> None:
    """Give back a job a poll claimed but cannot hand out; free the runner."""
    # Conditional, as the requeue in _settle_idle_runner.
    await db.execute(
        update(WorkflowJob)
        .where(
            WorkflowJob.id == job_id,
            WorkflowJob.status == "in_progress",
            WorkflowJob.runner_id == runner.id,
        )
        .values(status="queued", runner_id=None, runner_name=None, started_at=None)
        .execution_options(synchronize_session=False)
    )
    runner.busy = False
    runner.status = "online"
    await db.commit()
    logger.info("Runner %s's poll was replaced while it claimed job %s; job requeued", runner.name, job_id)


async def _runner_moved_on(runner: Runner, job: WorkflowJob, db) -> bool:
    """Whether the runner now runs a job other than ``job``."""
    held = _holding.get(runner.id)
    if held is not None and held[0] != job.id:
        return True
    other = (await db.execute(
        select(WorkflowJob.id).where(
            WorkflowJob.runner_id == runner.id,
            WorkflowJob.status == "in_progress",
            WorkflowJob.id != job.id,
        ).limit(1)
    )).scalar_one_or_none()
    return other is not None


async def _complete_job(
    job: WorkflowJob, runner: Runner, result_str: str, db, outputs: dict | None = None,
) -> None:
    # A job cancelled while it ran keeps that conclusion when its runner
    # reports the end; the report still frees the runner, unless the runner
    # has already taken another job. The write is conditional on the stored
    # row, not on ``job``: cancellation writers do not take the runner's
    # lock, so one can commit after the caller loaded the job. Reported
    # outputs are written by the same conditional UPDATE, so a cancelled job
    # keeps the outputs it had.
    await db.flush()
    values = {
        "status": "completed",
        "conclusion": _result_to_conclusion(result_str),
        "completed_at": datetime.now(timezone.utc),
    }
    if outputs:
        values["outputs"] = {**(job.outputs or {}), **outputs}
    finished = await db.execute(
        update(WorkflowJob)
        .where(
            WorkflowJob.id == job.id,
            or_(
                WorkflowJob.status != "completed",
                WorkflowJob.conclusion.is_(None),
                WorkflowJob.conclusion != "cancelled",
            ),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    await db.refresh(job)
    if finished.rowcount == 1 and _is_success_result(result_str):
        _complete_queued_steps(job, result_str)

    if await _runner_moved_on(runner, job, db):
        # A late report of a job's end can land after the runner took its
        # next job. Freeing it then hid that job's cancellation, and offered
        # the runner a second job while it ran one.
        logger.info("Runner %s reported job %s after taking another; left busy", runner.name, job.id)
    else:
        if _holding.get(runner.id, (None,))[0] == job.id:
            del _holding[runner.id]
        runner.busy = False
        runner.status = "online"
    logger.info("Runner %s reported job %s: %s (%s)", runner.name, job.id, result_str, job.conclusion)
    await db.flush()

    await dispatch_ready_jobs(db, job.run_id)
    await check_run_completion(db, job.run_id)
    await db.flush()


async def _report_job_end(
    job: WorkflowJob, runner: Runner, result_str: str, db, outputs: dict | None = None,
) -> bool:
    """A runner's report of a job's end, serialised with its polls.

    Under the runner's lock, so the report sees a job a poll handed the
    runner meanwhile and does not free the runner while it runs that job.

    False, and nothing changed, when the job is no longer the runner's once
    the lock is held: a poll requeued it (the runner never received it) or
    another runner took it while this report waited. Ownership is checked
    again here because those writers hold the lock, so a check made before
    waiting can be stale.
    """
    async with _runner_lock(runner.id):
        owner = (await db.execute(
            select(WorkflowJob.runner_id)
            .where(WorkflowJob.id == job.id)
            .execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if owner != runner.id:
            await db.commit()
            logger.info("Runner %s reported job %s, which it no longer holds; ignored", runner.name, job.id)
            return False
        await _complete_job(job, runner, result_str, db, outputs)
        await db.commit()
        return True


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
    # Before any await: the order polls arrived in, not the order they
    # finished setting up, decides which one is the runner's newest.
    generation = next(_poll_arrivals)
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

    runner_id = runner.id
    # A newer poll that registered first keeps its place; this one then
    # finds itself replaced on its first pass.
    _poll_generation[runner_id] = max(_poll_generation.get(runner_id, 0), generation)
    lock = _runner_lock(runner_id)
    status = request.query_params.get("status", "")
    last_message_id = request.query_params.get("lastMessageId", "")

    deadline = asyncio.get_event_loop().time() + _POLL_SECONDS
    while asyncio.get_event_loop().time() < deadline:
        async with lock:
            if _poll_generation.get(runner_id) != generation or await request.is_disconnected():
                # A newer poll from this runner replaced this one.
                return Response(status_code=204)
            # Every pass, not only the first: a lost hand-out is decided once
            # its acknowledgement window has passed.
            await _settle_idle_runner(runner, status, last_message_id, db)
            # End the read transaction: under WAL a write in a transaction
            # that began by reading fails at once when another writer
            # committed meanwhile, without waiting on busy_timeout.
            await db.commit()
            cancellation = await _pending_cancellation(runner, db)
            if cancellation is not None:
                logger.info(
                    "Runner %s sent cancellation of job %s",
                    runner.name, cancellation["messageId"] - _CANCELLATION_MESSAGE_BASE,
                )
                return cancellation
            # Again, right before a hand-out: a newer poll can start while
            # this one awaited the database above, and the runner abandoned
            # this one when it did.
            if _poll_generation.get(runner_id) != generation or await request.is_disconnected():
                return Response(status_code=204)
            # This poll loaded the runner before it waited for the lock; an
            # older poll may have handed it a job since (_claim_next_job
            # hands nothing to a busy runner).
            await db.refresh(runner, attribute_names=["busy", "status"])
            await db.commit()
            message = await _claim_next_job(runner, db, _request_base(request))
            if not isinstance(message, Response):
                body = json.loads(message["body"])
                if _poll_generation.get(runner_id) != generation or await request.is_disconnected():
                    # A newer poll started while this one claimed the job:
                    # the runner reads only the newer one.
                    await _release_claim(runner, int(body["requestId"]), db)
                    return Response(status_code=204)
                _holding[runner_id] = (int(body["requestId"]), str(body["jobId"]))
                logger.info("Runner %s acquired job %s", runner.name, body["requestId"])
                return message

        # End the read transaction: under SQLite WAL it pins the snapshot it
        # started with, and the next pass must see a cancellation or a newly
        # queued job committed meanwhile.
        await db.commit()
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)

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
    await _get_runner_from_token(request, db)
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

    if not await _report_job_end(job, runner, body.get("result", "Succeeded"), db):
        raise HTTPException(status_code=404, detail="Job not found")
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
    runner = await _get_runner_from_token(request, db)
    # Under the runner's lock: a poll deciding whether the runner received
    # its job must not miss an acknowledgement landing mid-decision.
    async with _runner_lock(runner.id):
        if message_id >= _CANCELLATION_MESSAGE_BASE:
            job_id = message_id - _CANCELLATION_MESSAGE_BASE
            _cancellation_acknowledged[runner.id] = job_id
            logger.info("Runner %s acknowledged cancellation of job %s", runner.name, job_id)
        elif _holding.get(runner.id, (None,))[0] == message_id:
            _acknowledged[runner.id] = message_id
            logger.info("Runner %s acknowledged job %s", runner.name, message_id)
    return {"messageId": message_id, "status": "deleted"}


@router.delete("/_apis/distributedtask/pools/{pool_id}/sessions/{session_id}")
@router.delete("/{owner}/{repo}/_apis/distributedtask/pools/{pool_id}/sessions/{session_id}")
async def dt_delete_pool_session(
    pool_id: int, session_id: str, request: Request, db: DbSession,
):
    """Pool-scoped session cleanup."""
    return await dt_delete_session(session_id, request, db)


def _job_request_not_found(request_id: int) -> JSONResponse:
    """The job request is gone or held by another runner.

    The runner stops renewing its lock, and abandons the job, only on
    TaskAgentJobNotFoundException. A bare 404 is retried, so a runner that
    lost the job would keep renewing instead of polling for another one.
    """
    response = _wrapped_exception(
        404,
        "TaskAgentJobNotFoundException",
        f"Job request {request_id} was not found or is assigned to another runner.",
    )
    # Kept from the earlier plain 404 for clients that read it; the runner
    # reads typeKey and ignores it.
    body = json.loads(response.body)
    body["detail"] = "Job request not found"
    return JSONResponse(status_code=404, content=body)


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
        return _job_request_not_found(request_id)
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
        return _job_request_not_found(request_id)

    query_result = request.query_params.get("result")
    if "result" in body and body["result"] is not None:
        result_str = body["result"]
    elif query_result is not None:
        result_str = query_result
    elif str(body.get("state", "")).lower() == "completed":
        result_str = body.get("result", "Succeeded")
    else:
        result_str = None
    if result_str is not None and not await _report_job_end(job, runner, result_str, db):
        return _job_request_not_found(request_id)
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
        return _job_request_not_found(request_id)

    if not await _report_job_end(job, runner, request.query_params.get("result", "Succeeded"), db):
        return _job_request_not_found(request_id)
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
    job = await _job_for_plan(request, db, plan_id, timeline_id)

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


def _wrapped_exception(status: int, type_key: str, message: str) -> JSONResponse:
    """An error body in the shape the runner's VssHttpClient unwraps.

    `typeKey` selects the exception class on the runner side, which is how a
    missing action becomes UnresolvableActionDownloadInfoException (no
    retries) rather than a generic failure retried three times with backoff.
    """
    return JSONResponse(
        status_code=status,
        content={
            "$id": "1",
            "innerException": None,
            "message": message,
            "typeName": f"GitHub.DistributedTask.WebApi.{type_key}, GitHub.DistributedTask.WebApi",
            "typeKey": type_key,
            "errorCode": 0,
            "eventId": 3000,
        },
    )


@router.post("/_apis/distributedtask/hubs/{hub_name}/plans/{plan_id}/events")
@router.post("/{owner}/{repo}/_apis/distributedtask/hubs/{hub_name}/plans/{plan_id}/events")
async def dt_raise_plan_event(
    hub_name: str, plan_id: str, request: Request, db: DbSession,
):
    """Complete the job from the worker's JobCompleted event.

    The upstream runner resolves a job's ``outputs:`` itself and sends them
    only in this event, which it raises from plan version 8; at that version
    the listener no longer finishes the job request, so this event is the
    job's completion too. The outputs are stored first, so the jobs that need
    this one are released with them. A secret output is left out, as GitHub
    leaves it out. The event completes a job and sets the outputs the jobs
    that need it read, so the job token's signature and expiry are checked.
    """
    auth = request.headers.get("Authorization", "")
    validated = await validate_job_token(db, auth[7:]) if auth.startswith("Bearer ") else None
    if validated is None:
        raise HTTPException(
            status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Bearer"}
        )
    job, _run = validated
    # The plan in the path is the job's own run, as on every job-server route.
    if plan_id != _workflow_guid("run", job.run_id):
        raise HTTPException(status_code=404, detail="Plan not found")
    body = await request.json()
    name = body.get("name", body.get("Name", ""))
    if name != "JobCompleted":
        return Response(status_code=200)
    event_job = str(body.get("jobId", body.get("JobId", "")) or "")
    if event_job and event_job.lower() != _workflow_guid("job", job.id).lower():
        raise HTTPException(status_code=403, detail="Event is for another job")
    # Only a running job completes. A repeat after completion, or an event
    # for a job that is queued again, changes neither its outputs nor its
    # result: the jobs that need it may already have read them. A job
    # cancelled while it ran still reaches _complete_job, which keeps the
    # cancelled conclusion and frees the runner; its outputs stay unchanged.
    if job.status != "completed" or job.conclusion == "cancelled":
        if job.status not in ("in_progress", "completed"):
            return Response(status_code=200)
    else:
        return Response(status_code=200)
    runner = (await db.execute(select(Runner).where(Runner.id == job.runner_id))).scalar_one_or_none()
    if runner is None:
        raise HTTPException(status_code=409, detail="Job has no runner to complete it")
    result_str = body.get("result", body.get("Result", "Succeeded"))
    resolved = {}
    if job.status != "completed":
        outputs = body.get("outputs", body.get("Outputs")) or {}
        for key, value in outputs.items():
            if isinstance(value, dict):
                if value.get("isSecret", value.get("IsSecret")):
                    continue
                value = value.get("value", value.get("Value"))
            resolved[key] = "" if value is None else str(value)
    # Under the runner's lock, as every report of a job's end. A job that
    # moved off the runner, or was cancelled, while this waited is left as
    # it is, outputs included, as a job queued again is above.
    await _report_job_end(job, runner, result_str, db, resolved)
    return Response(status_code=200)


@router.post("/_apis/distributedtask/hubs/{hub_name}/plans/{plan_id}/actionsdownloadinfo")
@router.post("/{owner}/{repo}/_apis/distributedtask/hubs/{hub_name}/plans/{plan_id}/actionsdownloadinfo")
async def dt_resolve_action_download_info(
    hub_name: str, plan_id: str, request: Request, db: DbSession,
):
    """Resolve every `uses:` action in the job to a commit and an archive URL.

    Called once by ActionManager before the first step runs, with the
    repository references it found in the job. The archive URL points back
    here: the runner downloads it with the job token as a Basic credential,
    which GitHub would reject and this service can verify.
    """
    job = await _job_for_plan(request, db, plan_id)
    body = await request.json()
    references = body.get("actions") or body.get("Actions") or []
    base = _runner_reachable_base_url(_request_base(request))

    resolved: dict[str, dict] = {}
    for reference in references:
        name = reference.get("nameWithOwner") or reference.get("NameWithOwner") or ""
        ref = reference.get("ref") or reference.get("Ref") or ""
        if not name or not ref:
            return _wrapped_exception(
                400, "NonRetryableActionDownloadInfoException",
                f"action reference needs nameWithOwner and ref: {reference!r}",
            )
        # A repository on this emulator first, then upstream. GHES resolves a
        # repository it holds locally and goes to github.com only for one it
        # lacks; this emulator also goes upstream when a public repository is
        # here but lacks the ref, so a partial local copy of a public action
        # works. A private or internal repository here that lacks the ref is
        # unresolvable, and its name is not looked up upstream.
        held, local = await _local_action_repository(name, job, db)
        if held and local is None:
            # Here, but not usable by this job's repository: as unresolvable as
            # on GitHub, and not looked up upstream under the same name.
            return _wrapped_exception(
                404, "UnresolvableActionDownloadInfoException",
                f"Unable to resolve action {name}@{ref}, repository not found",
            )
        sha = await resolve_local_action_sha(local.disk_path, ref) if local else None
        if sha is None and local is not None and not _is_public(local):
            return _wrapped_exception(
                404, "UnresolvableActionDownloadInfoException",
                f"Unable to resolve action {name}@{ref}, unable to find version {ref}",
            )
        if sha is None:
            try:
                sha = await resolve_action_sha(name, ref)
            except UpstreamActionError as exc:
                if exc.not_found:
                    return _wrapped_exception(
                        404, "UnresolvableActionDownloadInfoException", str(exc)
                    )
                return _wrapped_exception(502, "DistributedTaskException", str(exc))
        archive = f"{base}/_apis/distributedtask/actions/archives/{name}/{sha}/archive"
        resolved[f"{name}@{ref}"] = {
            "nameWithOwner": name,
            "ref": ref,
            "resolvedNameWithOwner": name,
            "resolvedSha": sha,
            "tarballUrl": f"{archive}.tar.gz",
            "zipballUrl": f"{archive}.zip",
        }
    return {"actions": resolved}


async def _local_action_repository(
    name_with_owner: str, job: WorkflowJob, db,
) -> tuple[bool, Repository | None]:
    """Whether this emulator holds the repository a `uses:` reference names,
    and that repository when the job may use it as an action.

    As GitHub does by default, a workflow may use an action from a public
    repository or from its own repository. GitHub lets other repositories use
    a private or internal repository's actions only when its owner enables
    that access; this emulator has no such setting, so any other private or
    internal repository, the same owner's included, is not usable.
    """
    repository = (await db.execute(
        select(Repository)
        .where(func.lower(Repository.full_name) == name_with_owner.lower())
        .order_by(Repository.id)
    )).scalars().first()
    if repository is None or not repository.disk_path:
        return False, None
    if _is_public(repository):
        return True, repository
    job_repository = (await db.execute(
        select(Repository)
        .join(WorkflowRun, WorkflowRun.repo_id == Repository.id)
        .where(WorkflowRun.id == job.run_id)
    )).scalars().first()
    if job_repository is not None and job_repository.id == repository.id:
        return True, repository
    return True, None


def _is_public(repository: Repository) -> bool:
    return not repository.private and (repository.visibility or "public") == "public"


@router.get("/_apis/distributedtask/actions/archives/{owner}/{repo}/{sha}/archive.{fmt}")
@router.get("/{owner_}/{repo_}/_apis/distributedtask/actions/archives/{owner}/{repo}/{sha}/archive.{fmt}")
async def dt_action_archive(
    owner: str, repo: str, sha: str, fmt: str, request: Request, db: DbSession,
):
    """Serve an action archive: built from a repository here when it holds the
    commit, else fetched from upstream once per SHA. A private or internal
    repository here that lacks the commit is 404, not fetched upstream."""
    job = await _get_job_from_download_credential(request, db)
    name = f"{owner}/{repo}"
    held, local = await _local_action_repository(name, job, db)
    if held and local is None:
        # The same rule as when the download was resolved, since a job can
        # also request this URL directly, without resolving it first.
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        if local and await resolve_local_action_sha(local.disk_path, sha) == sha:
            path = await build_local_action_archive(local.disk_path, name, sha, fmt)
        elif local is not None and not _is_public(local):
            raise HTTPException(status_code=404, detail="Not Found")
        else:
            path = await fetch_action_archive(name, sha, fmt)
    except UpstreamActionError as exc:
        raise HTTPException(status_code=404 if exc.not_found else 502, detail=str(exc)) from exc
    media = "application/gzip" if fmt == "tar.gz" else "application/zip"
    return FileResponse(path, media_type=media, filename=f"{repo}-{sha[:7]}.{fmt}")


@router.post("/_apis/distributedtask/hubs/{hub_name}/plans/{plan_id}/logs")
@router.post("/{owner}/{repo}/_apis/distributedtask/hubs/{hub_name}/plans/{plan_id}/logs")
async def dt_create_hub_plan_log(
    hub_name: str, plan_id: str, request: Request, db: DbSession,
):
    """Create a log for one of the job's timeline records.

    The worker creates a log per step, then appends the step's whole log file
    to it (JobServerQueue.UploadFile). It deserializes the answer into a
    TaskLog, whose constructor requires the path: an answer without one threw
    "Value cannot be null. (Parameter 'path')" and no step log was uploaded.
    """
    job = await _job_for_plan(request, db, plan_id)
    content_type = request.headers.get("content-type", "")
    if "application/octet-stream" in content_type:
        log_id = _next_step_log_id(job.id)
        _append_step_log(job.id, log_id, await request.body())
        return _task_log(log_id, f"logs/{log_id}")

    body = await request.json()
    log_id = _next_step_log_id(job.id)
    return _task_log(log_id, str(body.get("path", body.get("Path", "")) or f"logs/{log_id}"))


def _step_log_dir(job_id: int) -> str:
    """Where a job's uploaded step logs live, one file per log id.

    Kept apart from the job log, which the live console feed already writes:
    the uploaded step logs repeat those lines, and appending them there
    printed every line twice.
    """
    return os.path.join(os.path.dirname(_job_log_path(job_id)), str(job_id))


def _next_step_log_id(job_id: int) -> int:
    directory = _step_log_dir(job_id)
    os.makedirs(directory, exist_ok=True)
    taken = [int(name.split(".")[0]) for name in os.listdir(directory) if name.split(".")[0].isdigit()]
    log_id = max(taken, default=0) + 1
    # Reserve the id now: the worker appends to it in a later request.
    open(os.path.join(directory, f"{log_id}.log"), "ab").close()
    return log_id


def _append_step_log(job_id: int, log_id: int, data: bytes) -> None:
    directory = _step_log_dir(job_id)
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, f"{log_id}.log"), "ab") as f:
        f.write(data)


def _task_log(log_id: int, path: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {"id": log_id, "path": path, "lineCount": 0, "createdOn": now, "lastChangedOn": now}


@router.post("/_apis/distributedtask/hubs/{hub_name}/plans/{plan_id}/logs/{log_id}")
@router.post("/{owner}/{repo}/_apis/distributedtask/hubs/{hub_name}/plans/{plan_id}/logs/{log_id}")
async def dt_append_hub_plan_log(
    hub_name: str,
    plan_id: str,
    log_id: int,
    request: Request,
    db: DbSession,
):
    """Append an uploaded step log file to the log the worker created for it."""
    job = await _job_for_plan(request, db, plan_id)
    _append_step_log(job.id, log_id, await request.body())
    return _task_log(log_id, f"logs/{log_id}")


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
    job = await _job_for_plan(request, db, plan_id, timeline_id)
    body = await request.json()

    await _update_timeline_for_job(job, body, db)
    await db.commit()
    # The worker reads this as VssJsonCollectionWrapper<List<TimelineRecord>>,
    # {"count", "value"}. A bare array failed to deserialize, so it treated
    # every update as failed and kept resending it.
    records = body.get("value", body.get("records", []))
    return {"count": len(records), "value": records}
