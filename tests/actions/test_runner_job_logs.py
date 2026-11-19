"""The upstream runner's console feed, step logs and timeline land on its job.

Three things went wrong on this path:
- The job was looked up by plan, which is the whole run, so with two jobs
  running at once one job's lines landed in the other's log and the first
  job's log was missing.
- The create-log answer had no path. The worker's TaskLog requires one, so
  every step-log upload threw.
- Timeline updates were answered with a bare array instead of the collection
  wrapper the worker reads, so it treated each update as failed.
"""

import json
import os

import pytest
from sqlalchemy import select

from app.api import actions_distributed_task as dt
from app.models.actions import Workflow
from app.models.repository import Repository
from app.services.workflow_service import create_workflow_run
from tests.conftest import API, auth_headers

REPO = "testuser/logs-repo"


@pytest.fixture
async def two_job_run(client, db_session, test_user, test_token):
    resp = await client.post(
        f"{API}/user/repos", json={"name": "logs-repo"}, headers=auth_headers(test_token)
    )
    assert resp.status_code == 201
    repo = (await db_session.execute(
        select(Repository).where(Repository.full_name == REPO)
    )).scalar_one()
    workflow = Workflow(repo_id=repo.id, name="Two", path=".github/workflows/two.yml")
    db_session.add(workflow)
    await db_session.flush()
    await create_workflow_run(
        db_session, workflow, {
            "name": "Two", "on": ["push"],
            "jobs": {
                name: {"runs-on": ["self-hosted"], "steps": [{"run": f"echo {name}"}]}
                for name in ("first", "second")
            },
        }, event="push", payload={"ref": "refs/heads/main"}, actor=test_user,
        head_sha="abc123", head_branch="main",
    )
    await db_session.commit()


async def _claim(client, test_token, agent_name):
    token = (await client.post(
        f"{API}/repos/{REPO}/actions/runners/registration-token", headers=auth_headers(test_token),
    )).json()["token"]
    agent = (await client.post(
        "/_apis/distributedtask/pools/1/agents",
        json={"token": token, "agentName": agent_name, "labels": [{"name": "self-hosted"}], "os": "linux"},
    )).json()
    headers = {"Authorization": f"Bearer {agent['token']}"}
    session = (await client.post(
        "/_apis/distributedtask/pools/1/sessions", json={"agent": {"id": agent["id"]}}, headers=headers,
    )).json()
    message = (await client.get(
        f"/_apis/distributedtask/pools/1/sessions/{session['sessionId']}/messages",
        params={"lastMessageId": 0}, headers=headers,
    )).json()
    body = json.loads(message["body"])
    return body, {"Authorization": f"Bearer {body['variables']['system.github.token']['Value']}"}


def _job_log(job_id: int) -> str:
    path = dt._job_log_path(job_id)
    return open(path).read() if os.path.exists(path) else ""


def _job_id(body: dict) -> int:
    return int(body["requestId"])


@pytest.mark.asyncio
async def test_each_jobs_console_lines_land_in_its_own_log(client, two_job_run, test_token):
    first, first_auth = await _claim(client, test_token, "runner-a")
    second, second_auth = await _claim(client, test_token, "runner-b")
    # One run, so one plan for both jobs.
    assert first["plan"]["planId"] == second["plan"]["planId"]
    assert _job_id(first) != _job_id(second)

    plan = first["plan"]["planId"]
    for body, auth, line in ((first, first_auth, "from-first"), (second, second_auth, "from-second")):
        timeline = body["timeline"]["id"]
        resp = await client.post(
            f"/_apis/distributedtask/hubs/Build/plans/{plan}/timelines/{timeline}/records/{timeline}/feed",
            json={"value": [line], "count": 1}, headers=auth,
        )
        assert resp.status_code == 204, resp.text

    assert "from-first" in _job_log(_job_id(first)) and "from-second" not in _job_log(_job_id(first))
    assert "from-second" in _job_log(_job_id(second)) and "from-first" not in _job_log(_job_id(second))


@pytest.mark.asyncio
async def test_a_created_log_carries_its_path_and_a_distinct_id(client, two_job_run, test_token):
    body, auth = await _claim(client, test_token, "runner-a")
    plan = body["plan"]["planId"]
    created = []
    for record in ("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"):
        resp = await client.post(
            f"/_apis/distributedtask/hubs/Build/plans/{plan}/logs",
            json={"path": f"logs\\{record}"}, headers=auth,
        )
        assert resp.status_code == 200, resp.text
        created.append(resp.json())
    assert [log["path"] for log in created] == [
        "logs\\00000000-0000-0000-0000-000000000001",
        "logs\\00000000-0000-0000-0000-000000000002",
    ]
    assert created[0]["id"] != created[1]["id"]
    assert all("createdOn" in log and "lastChangedOn" in log for log in created)


@pytest.mark.asyncio
async def test_an_uploaded_step_log_is_kept_without_doubling_the_job_log(
    client, two_job_run, test_token
):
    body, auth = await _claim(client, test_token, "runner-a")
    plan = body["plan"]["planId"]
    timeline = body["timeline"]["id"]
    await client.post(
        f"/_apis/distributedtask/hubs/Build/plans/{plan}/timelines/{timeline}/records/{timeline}/feed",
        json={"value": ["live-line"], "count": 1}, headers=auth,
    )
    log = (await client.post(
        f"/_apis/distributedtask/hubs/Build/plans/{plan}/logs", json={"path": "logs\\x"}, headers=auth,
    )).json()
    resp = await client.post(
        f"/_apis/distributedtask/hubs/Build/plans/{plan}/logs/{log['id']}",
        content=b"live-line\n", headers={**auth, "content-type": "application/octet-stream"},
    )
    assert resp.status_code == 200
    assert resp.json()["path"]
    assert _job_log(_job_id(body)).count("live-line") == 1
    step_log = os.path.join(dt._step_log_dir(_job_id(body)), f"{log['id']}.log")
    assert open(step_log, "rb").read() == b"live-line\n"


@pytest.mark.asyncio
async def test_timeline_updates_are_answered_as_a_collection(client, two_job_run, test_token):
    body, auth = await _claim(client, test_token, "runner-a")
    plan = body["plan"]["planId"]
    timeline = body["timeline"]["id"]
    records = [{"id": timeline, "state": "inProgress"}]
    resp = await client.patch(
        f"/_apis/distributedtask/hubs/Build/plans/{plan}/timelines/{timeline}/records",
        json={"value": records, "count": 1}, headers=auth,
    )
    assert resp.status_code == 200
    assert resp.json() == {"count": 1, "value": records}
