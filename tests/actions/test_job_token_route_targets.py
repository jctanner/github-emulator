"""A job token reaches only its own job's plan and timeline.

The upstream runner sends each job-server call with the job's own token and
the plan (the run) and timeline (the job) ids from its job message. A route
answers 404 when the plan or timeline in its path is not the token's, and
writes only to the token's job.
"""

import os

import pytest
from sqlalchemy import select

from app.api.actions_distributed_task import _job_log_path
from app.api.actions_runner_protocol import _workflow_guid
from app.models.actions import Workflow, WorkflowJob
from app.models.repository import Repository
from app.services.job_token_service import issue_job_token
from app.services.workflow_service import create_workflow_run
from tests.conftest import API, auth_headers


async def _run_with_jobs(client, db_session, test_user, test_token, name: str, job_keys: list[str]):
    resp = await client.post(f"{API}/user/repos", json={"name": name}, headers=auth_headers(test_token))
    assert resp.status_code == 201, resp.text
    repo = (await db_session.execute(
        select(Repository).where(Repository.full_name == f"testuser/{name}")
    )).scalar_one()
    workflow = Workflow(repo_id=repo.id, name="CI", path=".github/workflows/ci.yml")
    db_session.add(workflow)
    await db_session.flush()
    jobs = {key: {"runs-on": ["self-hosted"], "steps": [{"run": "true"}]} for key in job_keys}
    run = await create_workflow_run(
        db_session, workflow, {"name": "CI", "on": ["push"], "jobs": jobs}, event="push",
        payload={"ref": "refs/heads/main"}, actor=test_user, head_sha="abc123", head_branch="main",
    )
    rows = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run.id).order_by(WorkflowJob.id)
    )).scalars().all()
    for job in rows:
        job.status = "in_progress"
    await db_session.commit()
    return rows


def _target(job: WorkflowJob) -> dict:
    return {
        "token": issue_job_token(job),
        "plan": _workflow_guid("run", job.run_id),
        "timeline": _workflow_guid("timeline", job.id),
    }


def _calls(plan: str, timeline: str) -> dict[str, tuple[str, str, dict]]:
    hub = f"/_apis/distributedtask/hubs/Build/plans/{plan}"
    return {
        "timeline record feed": (
            "POST", f"{hub}/timelines/{timeline}/records/{timeline}/feed",
            {"json": {"value": ["line"], "count": 1}},
        ),
        "timeline records": ("PATCH", f"{hub}/timelines/{timeline}/records", {"json": {"value": [], "count": 0}}),
        "upload log": (
            "POST", f"{hub}/logs",
            {"content": b"line\n", "headers": {"content-type": "application/octet-stream"}},
        ),
        "append log": (
            "POST", f"{hub}/logs/1",
            {"content": b"line\n", "headers": {"content-type": "application/octet-stream"}},
        ),
        "action download info": ("POST", f"{hub}/actionsdownloadinfo", {"json": {"actions": []}}),
    }


async def _send(client, method, path, kwargs, token):
    headers = {**kwargs.get("headers", {}), "Authorization": f"Bearer {token}"}
    rest = {k: v for k, v in kwargs.items() if k != "headers"}
    return await client.request(method, path, headers=headers, **rest)


def _log_size(job: WorkflowJob) -> int:
    path = _job_log_path(job.id)
    return os.path.getsize(path) if os.path.exists(path) else 0


@pytest.mark.asyncio
async def test_a_job_token_reaches_only_its_own_plan(client, db_session, test_user, test_token):
    (mine,) = await _run_with_jobs(client, db_session, test_user, test_token, "plan-a", ["build"])
    (other,) = await _run_with_jobs(client, db_session, test_user, test_token, "plan-b", ["build"])
    me, them = _target(mine), _target(other)

    for route, (method, path, kwargs) in _calls(them["plan"], them["timeline"]).items():
        resp = await _send(client, method, path, kwargs, me["token"])
        assert resp.status_code == 404, f"{route} answered {resp.status_code} for another run's plan"
    assert _log_size(other) == 0

    for route, (method, path, kwargs) in _calls(me["plan"], me["timeline"]).items():
        resp = await _send(client, method, path, kwargs, me["token"])
        assert resp.status_code < 400, f"{route} refused the job's own plan: {resp.text}"
    assert _log_size(mine) > 0


@pytest.mark.asyncio
async def test_a_job_token_reaches_only_its_own_timeline_in_a_shared_run(
    client, db_session, test_user, test_token,
):
    first, second = await _run_with_jobs(client, db_session, test_user, test_token, "two-jobs", ["one", "two"])
    me, them = _target(first), _target(second)
    assert me["plan"] == them["plan"]

    calls = _calls(me["plan"], them["timeline"])
    for route in ("timeline record feed", "timeline records"):
        method, path, kwargs = calls[route]
        resp = await _send(client, method, path, kwargs, me["token"])
        assert resp.status_code == 404, f"{route} answered {resp.status_code} for another job's timeline"

    # Calls that name only the plan write to the token's own job.
    for route in ("upload log", "append log"):
        method, path, kwargs = calls[route]
        resp = await _send(client, method, path, kwargs, me["token"])
        assert resp.status_code < 400, resp.text
    assert _log_size(second) == 0
    assert _log_size(first) > 0
