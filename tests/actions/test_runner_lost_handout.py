"""A job is handed out only to a runner's live poll, and one handed to a poll
nobody read goes back to the queue rather than failing unrun.

The real runner abandons its message poll whenever its status changes and
opens a new one; the server keeps running the abandoned request. A job that
request claimed was answered to nobody: it sat in progress on a runner that
never saw it, and later failed with every step still queued.
"""

import asyncio
import json
import random
from collections import Counter

import pytest
from sqlalchemy import select, update

from app.api import actions_distributed_task as dt
from app.models.actions import Runner, Workflow, WorkflowJob
from app.models.repository import Repository
from app.services.workflow_service import create_workflow_run
from tests.conftest import auth_headers

API = "/api/v3"


@pytest.fixture(autouse=True)
def _fast_broker(monkeypatch):
    monkeypatch.setattr(dt, "_POLL_SECONDS", 1.0)
    monkeypatch.setattr(dt, "_POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(dt, "_ACK_WINDOW", dt.timedelta(seconds=0.2))
    states = (dt._holding, dt._cancellations_sent, dt._poll_generation, dt._runner_locks, dt._acknowledged)
    for state in states:
        state.clear()
    yield
    for state in states:
        state.clear()


async def _workflow(client, db_session, test_user, test_token, jobs: int):
    resp = await client.post(f"{API}/user/repos", json={"name": "broker-repo"}, headers=auth_headers(test_token))
    assert resp.status_code == 201
    repo = (await db_session.execute(
        select(Repository).where(Repository.full_name == "testuser/broker-repo")
    )).scalar_one()
    workflow = Workflow(repo_id=repo.id, name="Broker", path=".github/workflows/broker.yml")
    db_session.add(workflow)
    await db_session.flush()
    workflow_yaml = {
        "name": "Broker",
        "on": ["push"],
        "jobs": {
            f"job{n}": {"runs-on": ["self-hosted", "linux"], "steps": [{"run": "true"}]}
            for n in range(jobs)
        },
    }
    run = await create_workflow_run(
        db_session, workflow, workflow_yaml, event="push",
        payload={"ref": "refs/heads/main"}, actor=test_user,
        head_sha="abc123", head_branch="main",
    )
    await db_session.commit()
    return run


async def _runner(client, test_token, name):
    token = (await client.post(
        f"{API}/repos/testuser/broker-repo/actions/runners/registration-token",
        headers=auth_headers(test_token),
    )).json()["token"]
    registered = (await client.post(
        "/_apis/distributedtask/pools/1/agents",
        json={"token": token, "agentName": name,
              "labels": [{"name": "self-hosted"}, {"name": "linux"}], "os": "linux"},
    )).json()
    headers = {"Authorization": f"Bearer {registered['token']}"}
    session = (await client.post(
        "/_apis/distributedtask/pools/1/sessions",
        json={"agent": {"id": registered["id"]}}, headers=headers,
    )).json()
    return headers, session["sessionId"]


async def _retrying(call):
    """As a client does with the emulator's 503 for SQLite writer contention."""
    for _ in range(20):
        resp = await call()
        if resp.status_code != 503:
            return resp
        await asyncio.sleep(0.05)
    return resp


def _poll(client, headers, session_id, last, status="Online"):
    return client.get(
        f"/_apis/distributedtask/pools/1/sessions/{session_id}/messages",
        params={"lastMessageId": last, "status": status}, headers=headers,
    )


@pytest.mark.asyncio
async def test_a_job_its_runner_never_received_is_requeued_not_failed(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    headers, session_id = await _runner(client, test_token, "r1")
    message = (await _poll(client, headers, session_id, 0)).json()
    job_id = int(json.loads(message["body"])["requestId"])

    # The response was lost: the runner polls again, its last message still 0.
    # It is given the same job again, now through a poll it reads.
    again = await _poll(client, headers, session_id, 0)
    assert again.status_code == 200
    assert int(json.loads(again.json()["body"])["requestId"]) == job_id

    db_session.expire_all()
    job = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.id == job_id))).scalar_one()
    assert job.status == "in_progress" and job.conclusion is None


def _first_poll(client, headers, session_id, status="Online"):
    """A runner that has had no message since it connected sends no lastMessageId."""
    return client.get(
        f"/_apis/distributedtask/pools/1/sessions/{session_id}/messages",
        params={"status": status}, headers=headers,
    )


@pytest.mark.asyncio
async def test_a_job_handed_to_an_abandoned_first_poll_is_requeued(
    client, db_session, test_user, test_token
):
    """bt-f2, run 43740835949: the runner's first long-poll timed out on its
    side; the emulator handed the job to it anyway. The runner reconnected and
    polled Online with no lastMessageId, and the job was failed after the grace
    period though the runner never saw it."""
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    headers, session_id = await _runner(client, test_token, "r1")
    lost = (await _first_poll(client, headers, session_id)).json()
    job_id = int(json.loads(lost["body"])["requestId"])
    await asyncio.sleep(0.3)  # past the acknowledgement window

    again = await _retrying(lambda: _first_poll(client, headers, session_id))
    assert again.status_code == 200
    assert int(json.loads(again.json()["body"])["requestId"]) == job_id

    db_session.expire_all()
    job = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.id == job_id))).scalar_one()
    assert job.status == "in_progress" and job.conclusion is None


@pytest.mark.asyncio
async def test_a_newer_poll_replaces_an_older_one_before_it_can_claim(
    client, db_session, test_user, test_token
):
    run = await _workflow(client, db_session, test_user, test_token, jobs=1)
    await db_session.execute(update(WorkflowJob).where(WorkflowJob.run_id == run.id).values(status="waiting"))
    await db_session.commit()
    headers, session_id = await _runner(client, test_token, "r1")

    older = asyncio.create_task(_poll(client, headers, session_id, 0, "Busy"))
    await asyncio.sleep(0.1)
    newer = asyncio.create_task(_poll(client, headers, session_id, 0))
    assert (await asyncio.wait_for(older, 1)).status_code == 204

    await db_session.execute(update(WorkflowJob).where(WorkflowJob.run_id == run.id).values(status="queued"))
    await db_session.commit()
    assert (await newer).status_code == 200


@pytest.mark.asyncio
async def test_every_job_runs_once_when_runners_abandon_polls(
    client, db_session, test_user, test_token
):
    """N runners, M jobs, polls abandoned at random as the real runner does:
    each job is delivered to a live poll exactly once and none fails unrun."""
    jobs = 12
    run_id = (await _workflow(client, db_session, test_user, test_token, jobs=jobs)).id
    runners = [await _runner(client, test_token, f"r{i}") for i in range(4)]
    rng = random.Random(1234)
    delivered: Counter[int] = Counter()
    abandoned: list[asyncio.Task] = []

    async def work(headers, session_id):
        last = 0
        while sum(delivered.values()) < jobs:
            poll = asyncio.create_task(_poll(client, headers, session_id, last))
            if rng.random() < 0.5:
                # Status changed: this poll is left to the server, its answer unread.
                await asyncio.sleep(rng.uniform(0, 0.05))
                abandoned.append(poll)
                poll = asyncio.create_task(_poll(client, headers, session_id, last))
            resp = await poll
            if resp.status_code != 200 or resp.json()["messageType"] != "PipelineAgentJobRequest":
                continue
            job_id = int(json.loads(resp.json()["body"])["requestId"])
            delivered[job_id] += 1
            last = job_id
            # As the runner does once it has dispatched the job.
            ack = await _retrying(lambda job_id=job_id: client.delete(
                f"/_apis/distributedtask/pools/1/messages/{job_id}", headers=headers))
            assert ack.status_code == 200
            await asyncio.sleep(rng.uniform(0, 0.03))
            done = await _retrying(lambda job_id=job_id: client.post(
                f"/_apis/distributedtask/jobs/{job_id}/complete",
                json={"result": "Succeeded"}, headers=headers,
            ))
            assert done.status_code == 200

    await asyncio.wait_for(asyncio.gather(*(work(h, s) for h, s in runners)), 60)
    await asyncio.gather(*abandoned)

    db_session.expire_all()
    rows = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.run_id == run_id))).scalars().all()
    assert len(rows) == jobs
    assert all(count == 1 for count in delivered.values()), delivered
    assert {(row.status, row.conclusion) for row in rows} == {("completed", "success")}, [
        (row.id, row.status, row.conclusion, row.runner_name) for row in rows
        if (row.status, row.conclusion) != ("completed", "success")
    ]
    assert not any(r.busy for r in (await db_session.execute(select(Runner))).scalars().all())
