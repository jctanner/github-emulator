"""A runner removing itself while it holds a job is removed, and the job fails.

The runner-protocol delete routes detach a runner's job history; a runner
still running a job was refused (422), so a runner whose worker died
without a report could never remove itself.
"""

import json

import pytest
from sqlalchemy import select

from app.api import actions_distributed_task as dt
from app.models.actions import Runner, WorkflowJob
from tests.actions.test_runner_lost_handout import _poll, _workflow
from tests.conftest import auth_headers

API = "/api/v3"


@pytest.fixture(autouse=True)
def _fresh_broker(monkeypatch):
    monkeypatch.setattr(dt, "_POLL_SECONDS", 0.5)
    monkeypatch.setattr(dt, "_POLL_INTERVAL_SECONDS", 0.02)
    dt._reset_broker_state()
    yield
    dt._reset_broker_state()


@pytest.mark.asyncio
async def test_a_runner_removing_itself_mid_job_is_removed_and_its_job_fails(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    token = (await client.post(
        f"{API}/repos/testuser/broker-repo/actions/runners/registration-token",
        headers=auth_headers(test_token),
    )).json()["token"]
    registered = (await client.post(
        "/_apis/distributedtask/pools/1/agents",
        json={"token": token, "agentName": "r1",
              "labels": [{"name": "self-hosted"}, {"name": "linux"}], "os": "linux"},
    )).json()
    runner_id = registered["id"]
    headers = {"Authorization": f"Bearer {registered['token']}"}
    session_id = (await client.post(
        "/_apis/distributedtask/pools/1/sessions",
        json={"agent": {"id": runner_id}}, headers=headers,
    )).json()["sessionId"]
    message = (await _poll(client, headers, session_id, 0)).json()
    job_id = int(json.loads(message["body"])["requestId"])

    resp = await client.delete(f"/_apis/distributedtask/pools/1/agents/{runner_id}", headers=headers)
    assert resp.status_code == 200

    db_session.expire_all()
    job = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.id == job_id))).scalar_one()
    assert (job.status, job.conclusion, job.runner_id) == ("completed", "failure", None)
    assert (await db_session.execute(select(Runner).where(Runner.id == runner_id))).scalar_one_or_none() is None
