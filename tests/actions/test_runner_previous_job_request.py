"""A runner reads its previous job request before it starts the next job.

When a new job arrives before the previous job's worker has exited, the
runner's JobDispatcher.EnsureDispatchFinished GETs the previous request. The
emulator had no GET for that path; its 405 made the runner kill the old
worker and drop the new job, which then failed as idle-while-holding.

These tests check the GET answers with the job's result, that a finished
job keeps its first conclusion and outputs whatever is reported after, and
that a Busy poll is handed no new job.
"""

import json

import pytest
from sqlalchemy import select

from app.api import actions_distributed_task as dt
from app.models.actions import WorkflowJob
from tests.actions.test_runner_broker_races import _ack, _cancel, _runner, _take_job
from tests.actions.test_runner_lost_handout import _poll, _workflow

ROUTE_PREFIXES = ("", "/testuser/broker-repo", "/enterprises/acme")


@pytest.fixture(autouse=True)
def _fast_broker(monkeypatch):
    monkeypatch.setattr(dt, "_POLL_SECONDS", 0.5)
    monkeypatch.setattr(dt, "_POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(dt, "_ACK_WINDOW", dt.timedelta(seconds=0.2))
    dt._reset_broker_state()
    yield
    dt._reset_broker_state()


def _job_request(client, headers, job_id, prefix="/enterprises/acme"):
    return client.get(f"{prefix}/_apis/distributedtask/pools/1/jobrequests/{job_id}", headers=headers)


def _report(client, headers, job_id, result="succeeded"):
    return client.patch(
        f"/_apis/distributedtask/pools/1/jobrequests/{job_id}",
        json={"result": result}, headers=headers,
    )


def _poll_without_status(client, headers, session_id, last):
    return client.get(
        f"/_apis/distributedtask/pools/1/sessions/{session_id}/messages",
        params={"lastMessageId": last}, headers=headers,
    )


async def _stored(db_session, job_id) -> WorkflowJob:
    db_session.expire_all()
    return (await db_session.execute(select(WorkflowJob).where(WorkflowJob.id == job_id))).scalar_one()


@pytest.mark.asyncio
async def test_a_job_delivered_while_the_previous_worker_exits_runs(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=2)
    _runner_id, headers, session_id = await _runner(client, test_token)
    first = await _take_job(client, headers, session_id)
    assert (await _report(client, headers, first)).status_code == 200

    # The worker has not exited yet; a poll that carries no status still
    # gets the next job.
    message = await _poll_without_status(client, headers, session_id, first)
    assert message.status_code == 200
    second = int(json.loads(message.json()["body"])["requestId"])
    assert second != first

    previous = await _job_request(client, headers, first)
    assert previous.status_code == 200, previous.text
    assert previous.json()["requestId"] == first
    assert previous.json()["result"] == "succeeded"
    assert previous.json()["finishTime"]

    assert (await _ack(client, headers, session_id, second)).status_code == 200
    assert (await _report(client, headers, second)).status_code == 200
    job = await _stored(db_session, second)
    assert (job.status, job.conclusion) == ("completed", "success")


def _plan_event(job_guid, result, outputs):
    return {"name": "JobCompleted", "jobId": job_guid, "requestId": 1, "result": result, "outputs": outputs}


@pytest.mark.asyncio
async def test_a_completed_job_keeps_its_first_conclusion_and_outputs(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    _runner_id, headers, session_id = await _runner(client, test_token)
    message = (await _poll(client, headers, session_id, 0)).json()
    body = json.loads(message["body"])
    job_id = int(body["requestId"])
    assert (await _ack(client, headers, session_id, job_id)).status_code == 200
    events = f"/_apis/distributedtask/hubs/Actions/plans/{body['plan']['planId']}/events"
    job_token = {"Authorization": f"Bearer {body['variables']['system.github.token']['Value']}"}

    done = await client.post(
        events, json=_plan_event(body["jobId"], "succeeded", {"stage": {"value": "first"}}), headers=job_token,
    )
    assert done.status_code == 200, done.text
    finished = await _stored(db_session, job_id)
    assert (finished.status, finished.conclusion) == ("completed", "success")
    assert finished.outputs == {"stage": "first"}
    completed_at = finished.completed_at

    later_reports = [
        lambda: _report(client, headers, job_id, "canceled"),
        lambda: client.post(
            events, json=_plan_event(body["jobId"], "failed", {"stage": {"value": "second"}}), headers=job_token,
        ),
        lambda: client.post(
            f"/_apis/distributedtask/jobs/{job_id}/complete", json={"result": "Failed"}, headers=headers,
        ),
    ]
    for later in later_reports:
        assert (await later()).status_code == 200
        job = await _stored(db_session, job_id)
        assert (job.status, job.conclusion) == ("completed", "success")
        assert job.outputs == {"stage": "first"}
        assert job.completed_at == completed_at

    assert (await _job_request(client, headers, job_id)).json()["result"] == "succeeded"


@pytest.mark.asyncio
async def test_a_busy_poll_gets_no_job_and_the_next_online_poll_does(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=2)
    _runner_id, headers, session_id = await _runner(client, test_token)
    first = await _take_job(client, headers, session_id)
    assert (await _report(client, headers, first)).status_code == 200

    assert (await _poll(client, headers, session_id, first, "Busy")).status_code == 204
    db_session.expire_all()
    [queued] = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.id != first)
    )).scalars().all()
    assert queued.status == "queued" and queued.runner_id is None

    message = await _poll(client, headers, session_id, first, "Online")
    assert message.status_code == 200
    second = int(json.loads(message.json()["body"])["requestId"])
    assert second == queued.id
    assert (await _ack(client, headers, session_id, second)).status_code == 200
    assert (await _report(client, headers, second)).status_code == 200
    job = await _stored(db_session, second)
    assert (job.status, job.conclusion) == ("completed", "success")

    assert (await _job_request(client, headers, first)).json()["result"] == "succeeded"


@pytest.mark.asyncio
async def test_every_route_form_answers_and_a_running_job_has_no_result(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    _runner_id, headers, session_id = await _runner(client, test_token)
    job_id = await _take_job(client, headers, session_id)

    for prefix in ROUTE_PREFIXES:
        resp = await _job_request(client, headers, job_id, prefix)
        assert resp.status_code == 200, (prefix, resp.text)
        assert resp.json()["requestId"] == job_id
        assert "result" not in resp.json()
        assert "finishTime" not in resp.json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reported", "conclusion", "task_result"),
    [
        ("succeeded", "success", "succeeded"),
        ("failed", "failure", "failed"),
        ("canceled", "cancelled", "canceled"),
        ("skipped", "skipped", "skipped"),
    ],
)
async def test_each_conclusion_maps_to_its_task_result(
    client, db_session, test_user, test_token, reported, conclusion, task_result
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    _runner_id, headers, session_id = await _runner(client, test_token)
    job_id = await _take_job(client, headers, session_id)
    assert (await _report(client, headers, job_id, reported)).status_code == 200

    job = await _stored(db_session, job_id)
    assert job.conclusion == conclusion
    resp = await _job_request(client, headers, job_id)
    assert resp.status_code == 200
    assert resp.json()["result"] == task_result
    assert resp.json()["finishTime"] == job.completed_at.isoformat()


@pytest.mark.asyncio
async def test_another_runners_job_and_a_missing_job_are_not_found(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    _runner_id, headers, session_id = await _runner(client, test_token)
    job_id = await _take_job(client, headers, session_id)
    _other_id, other_headers, _other_session = await _runner(client, test_token, "r2")

    for asker, request_id in ((other_headers, job_id), (headers, 999999)):
        resp = await _job_request(client, asker, request_id)
        assert resp.status_code == 404
        assert resp.json()["typeKey"] == "TaskAgentJobNotFoundException"


@pytest.mark.asyncio
async def test_a_job_request_read_needs_the_runner_token(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    _runner_id, headers, session_id = await _runner(client, test_token)
    job_id = await _take_job(client, headers, session_id)

    assert (await _job_request(client, {}, job_id)).status_code == 401


@pytest.mark.asyncio
async def test_a_busy_poll_still_gets_a_pending_cancellation(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    _runner_id, headers, session_id = await _runner(client, test_token)
    job_id = await _take_job(client, headers, session_id)
    await _cancel(db_session, job_id)

    resp = await _poll(client, headers, session_id, job_id, "Busy")
    assert resp.status_code == 200
    assert resp.json()["messageType"] == "JobCancellation"
