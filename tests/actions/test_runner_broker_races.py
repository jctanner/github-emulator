"""Races between a runner's polls, acknowledgements and reports.

The real runner abandons a poll whenever its status changes, deletes each
message it handled, and reports a job's end in its own request. Each test
forces one interleaving and checks the broker does not lose a cancellation,
hand a superseded poll a job, give a busy runner a second job, or decide
receipt or freeing while an acknowledgement or report is in flight.
"""

import asyncio
import json

import pytest
from sqlalchemy import delete, select, update

from app.api import actions_distributed_task as dt
from app.models.actions import Runner, WorkflowJob
from tests.actions.test_runner_lost_handout import _poll, _workflow
from tests.conftest import auth_headers

API = "/api/v3"


@pytest.fixture(autouse=True)
def _fast_broker(monkeypatch):
    monkeypatch.setattr(dt, "_POLL_SECONDS", 0.5)
    monkeypatch.setattr(dt, "_POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(dt, "_ACK_WINDOW", dt.timedelta(seconds=0.2))
    dt._reset_broker_state()
    yield
    dt._reset_broker_state()


async def _runner(client, test_token, name="r1"):
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
    return registered["id"], headers, session["sessionId"]


def _ack(client, headers, session_id, message_id):
    return client.delete(
        f"/_apis/distributedtask/pools/1/messages/{message_id}",
        params={"sessionId": session_id}, headers=headers,
    )


async def _take_job(client, headers, session_id):
    message = (await _poll(client, headers, session_id, 0)).json()
    job_id = int(json.loads(message["body"])["requestId"])
    assert (await _ack(client, headers, session_id, job_id)).status_code == 200
    return job_id


async def _cancel(db_session, job_id):
    await db_session.execute(
        update(WorkflowJob).where(WorkflowJob.id == job_id).values(status="completed", conclusion="cancelled")
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_a_cancellation_whose_response_was_lost_is_sent_again(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    _runner_id, headers, session_id = await _runner(client, test_token)
    job_id = await _take_job(client, headers, session_id)
    await _cancel(db_session, job_id)

    lost = await _poll(client, headers, session_id, job_id, "Busy")
    assert lost.json()["messageType"] == "JobCancellation"
    # The runner never read that response; its next poll gets it again.
    again = await _poll(client, headers, session_id, job_id, "Busy")
    assert again.status_code == 200
    assert again.json()["messageType"] == "JobCancellation"
    assert json.loads(again.json()["body"])["jobId"] == json.loads(lost.json()["body"])["jobId"]

    # Once the runner deletes the message, it is not sent again.
    assert (await _ack(client, headers, session_id, again.json()["messageId"])).status_code == 200
    assert (await _poll(client, headers, session_id, again.json()["messageId"], "Busy")).status_code == 204


@pytest.mark.asyncio
async def test_an_online_poll_sent_before_the_hand_out_does_not_drop_a_cancellation(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    _runner_id, headers, session_id = await _runner(client, test_token)
    job_id = await _take_job(client, headers, session_id)
    await _cancel(db_session, job_id)

    # A request the runner sent as Online before it took the job, landing now.
    late = await _poll(client, headers, session_id, 0, "Online")
    assert late.status_code == 200
    assert late.json()["messageType"] == "JobCancellation"


@pytest.mark.asyncio
async def test_a_poll_superseded_while_it_waited_on_the_database_hands_out_nothing(
    client, db_session, test_user, test_token, monkeypatch
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    runner_id, headers, session_id = await _runner(client, test_token)
    settle = dt._settle_idle_runner

    async def settle_then_superseded(runner, status, last_message_id, db):
        await settle(runner, status, last_message_id, db)
        # A newer poll from the runner starts while this one awaits.
        dt._poll_generation[runner_id] = dt._poll_generation.get(runner_id, 0) + 1

    monkeypatch.setattr(dt, "_settle_idle_runner", settle_then_superseded)
    assert (await _poll(client, headers, session_id, 0)).status_code == 204

    db_session.expire_all()
    job = (await db_session.execute(select(WorkflowJob))).scalars().one()
    assert job.status == "queued" and job.runner_id is None


@pytest.mark.asyncio
async def test_a_runner_an_older_poll_made_busy_is_not_handed_a_second_job(
    client, db_session, test_user, test_token, monkeypatch
):
    await _workflow(client, db_session, test_user, test_token, jobs=2)
    runner_id, headers, session_id = await _runner(client, test_token)
    first = (await db_session.execute(select(WorkflowJob).order_by(WorkflowJob.id))).scalars().first()
    settle = dt._settle_idle_runner
    claimed = []

    async def older_poll_claims_meanwhile(runner, status, last_message_id, db):
        await settle(runner, status, last_message_id, db)
        if not claimed:
            # This poll already loaded the runner idle; an older poll's claim
            # of the first job commits now.
            claimed.append(first.id)
            await db_session.execute(update(WorkflowJob).where(WorkflowJob.id == first.id).values(
                status="in_progress", runner_id=runner_id, runner_name="r1",
            ))
            await db_session.execute(update(Runner).where(Runner.id == runner_id).values(busy=True, status="busy"))
            await db_session.commit()
            dt._holding[runner_id] = (first.id, dt._workflow_guid("job", first.id))

    monkeypatch.setattr(dt, "_settle_idle_runner", older_poll_claims_meanwhile)
    assert (await _poll(client, headers, session_id, 0, "Busy")).status_code == 204

    db_session.expire_all()
    jobs = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.runner_id == runner_id))).scalars().all()
    assert [job.id for job in jobs] == [first.id]


async def _blocked_by_runner_lock(runner_id, call):
    """Run ``call`` while the runner's lock is held; it must wait for it."""
    lock = dt._runner_lock(runner_id)
    await lock.acquire()
    task = asyncio.create_task(call())
    try:
        await asyncio.sleep(0.2)
        waited = not task.done()
    finally:
        lock.release()
    resp = await task
    return waited, resp


@pytest.mark.asyncio
async def test_an_acknowledgement_waits_for_a_poll_deciding_receipt(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    runner_id, headers, session_id = await _runner(client, test_token)
    message = (await _poll(client, headers, session_id, 0)).json()
    job_id = int(json.loads(message["body"])["requestId"])

    waited, resp = await _blocked_by_runner_lock(runner_id, lambda: _ack(client, headers, session_id, job_id))
    assert waited
    assert resp.status_code == 200
    assert dt._acknowledged[runner_id] == job_id


@pytest.mark.asyncio
async def test_a_report_waits_for_a_poll_handing_out_a_job(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    runner_id, headers, session_id = await _runner(client, test_token)
    job_id = await _take_job(client, headers, session_id)

    def report():
        return client.patch(
            f"/_apis/distributedtask/pools/1/jobrequests/{job_id}",
            json={"result": "succeeded"}, headers=headers,
        )

    waited, resp = await _blocked_by_runner_lock(runner_id, report)
    assert waited
    assert resp.status_code == 200
    db_session.expire_all()
    runner = (await db_session.execute(select(Runner).where(Runner.id == runner_id))).scalar_one()
    assert not runner.busy


@pytest.mark.asyncio
async def test_acknowledgements_are_kept_per_runner_not_per_job(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=2)
    runner_id, headers, session_id = await _runner(client, test_token)
    for _ in range(2):
        job_id = await _take_job(client, headers, session_id)
        assert (await client.patch(
            f"/_apis/distributedtask/pools/1/jobrequests/{job_id}",
            json={"result": "succeeded"}, headers=headers,
        )).status_code == 200
    assert dict(dt._acknowledged) == {runner_id: job_id}


@pytest.mark.asyncio
async def test_a_poll_superseded_during_its_claim_hands_out_nothing(
    client, db_session, test_user, test_token, monkeypatch
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    runner_id, headers, session_id = await _runner(client, test_token)
    claim = dt._claim_next_job

    async def claim_then_superseded(runner, db, base_url=None):
        message = await claim(runner, db, base_url)
        # A newer poll from the runner starts while this one claims.
        dt._poll_generation[runner_id] = dt._poll_generation.get(runner_id, 0) + 1
        return message

    monkeypatch.setattr(dt, "_claim_next_job", claim_then_superseded)
    assert (await _poll(client, headers, session_id, 0)).status_code == 204

    db_session.expire_all()
    job = (await db_session.execute(select(WorkflowJob))).scalars().one()
    assert job.status == "queued" and job.runner_id is None
    runner = (await db_session.execute(select(Runner).where(Runner.id == runner_id))).scalar_one()
    assert not runner.busy and runner.status == "online"
    assert runner_id not in dt._holding


@pytest.mark.asyncio
async def test_a_cancellation_committed_while_a_report_waits_keeps_cancelled(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    runner_id, headers, session_id = await _runner(client, test_token)
    job_id = await _take_job(client, headers, session_id)

    # The report loads the job, then waits for the runner's lock; the
    # cancellation commits in between.
    lock = dt._runner_lock(runner_id)
    await lock.acquire()
    report = asyncio.create_task(client.patch(
        f"/_apis/distributedtask/pools/1/jobrequests/{job_id}",
        json={"result": "succeeded"}, headers=headers,
    ))
    try:
        await asyncio.sleep(0.2)
        assert not report.done()
        await _cancel(db_session, job_id)
    finally:
        lock.release()
    assert (await report).status_code == 200

    db_session.expire_all()
    job = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.id == job_id))).scalar_one()
    assert (job.status, job.conclusion) == ("completed", "cancelled")
    runner = (await db_session.execute(select(Runner).where(Runner.id == runner_id))).scalar_one()
    assert not runner.busy


@pytest.mark.asyncio
async def test_a_late_online_poll_keeps_the_cancellation_of_a_deleted_job(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    runner_id, headers, session_id = await _runner(client, test_token)
    job_id = await _take_job(client, headers, session_id)
    # Deleting the repository deletes its jobs while the runner runs one.
    await db_session.execute(delete(WorkflowJob).where(WorkflowJob.id == job_id))
    await db_session.commit()

    # A request the runner sent as Online before it took the job, landing now.
    late = await _poll(client, headers, session_id, 0, "Online")
    assert late.status_code == 200
    assert late.json()["messageType"] == "JobCancellation"
    assert json.loads(late.json()["body"])["jobId"] == dt._workflow_guid("job", job_id)

    # Once the runner acknowledges it, an Online poll frees the runner.
    assert (await _ack(client, headers, session_id, late.json()["messageId"])).status_code == 200
    assert (await _poll(client, headers, session_id, late.json()["messageId"], "Online")).status_code == 204
    db_session.expire_all()
    runner = (await db_session.execute(select(Runner).where(Runner.id == runner_id))).scalar_one()
    assert not runner.busy
