"""A runner whose job disappears, or which says it is idle, is not stuck busy.

Deleting a repository deletes its runs and jobs. A runner executing one of
those jobs got 401 on every call and "not found" on its lease, was never told
to stop, and stayed busy in the emulator, so it never got another job.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.api import actions_distributed_task as dt
from app.models.actions import Runner, WorkflowJob
from tests.actions.test_uses_steps import _claim_job, uses_workflow  # noqa: F401


@pytest.fixture(autouse=True)
def _fresh_runner_state():
    # Module state keyed by ids; each test's database starts ids at 1.
    dt._reset_broker_state()
    yield
    dt._reset_broker_state()


async def _claimed(client, db_session, test_token):
    body = await _claim_job(client, test_token)
    db_session.expire_all()
    job = (await db_session.execute(select(WorkflowJob))).scalars().first()
    runner = (await db_session.execute(select(Runner).where(Runner.id == job.runner_id))).scalar_one()
    return body, job, runner


@pytest.mark.asyncio
async def test_a_deleted_job_is_cancelled_on_its_runner(
    client, db_session, uses_workflow, test_token  # noqa: F811
):
    body, job, runner = await _claimed(client, db_session, test_token)
    assert dt._holding[runner.id] == (job.id, body["jobId"])

    # What deleting the repository does to its jobs.
    await db_session.execute(delete(WorkflowJob).where(WorkflowJob.id == job.id))
    await db_session.commit()

    message = await dt._pending_cancellation(runner, db_session)
    assert message["messageType"] == "JobCancellation"
    assert json.loads(message["body"])["jobId"] == body["jobId"]


@pytest.mark.asyncio
async def test_a_runner_that_says_online_is_freed_and_its_lost_job_fails(
    client, db_session, uses_workflow, test_token  # noqa: F811
):
    _body, job, runner = await _claimed(client, db_session, test_token)
    assert runner.busy and job.status == "in_progress"

    job_id, runner_id = job.id, runner.id
    job.started_at = datetime.now(UTC) - timedelta(minutes=5)
    await db_session.commit()
    await dt._settle_idle_runner(runner, "Online", str(job_id), db_session)

    db_session.expire_all()
    job = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.id == job_id))).scalar_one()
    runner = (await db_session.execute(select(Runner).where(Runner.id == runner_id))).scalar_one()
    assert not runner.busy
    assert (job.status, job.conclusion) == ("completed", "failure")


@pytest.mark.asyncio
async def test_a_runner_that_says_busy_is_left_alone(
    client, db_session, uses_workflow, test_token  # noqa: F811
):
    _body, _job, runner = await _claimed(client, db_session, test_token)
    runner_id = runner.id
    await dt._settle_idle_runner(runner, "Busy", "", db_session)
    db_session.expire_all()
    runner = (await db_session.execute(select(Runner).where(Runner.id == runner_id))).scalar_one()
    assert runner.busy


@pytest.mark.asyncio
async def test_a_runner_that_says_online_just_after_taking_a_job_keeps_it(
    client, db_session, uses_workflow, test_token  # noqa: F811
):
    # The real runner can poll once more as Online before it marks itself Busy.
    _body, job, runner = await _claimed(client, db_session, test_token)
    job_id, runner_id = job.id, runner.id
    await dt._settle_idle_runner(runner, "Online", str(job_id), db_session)
    db_session.expire_all()
    job = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.id == job_id))).scalar_one()
    runner = (await db_session.execute(select(Runner).where(Runner.id == runner_id))).scalar_one()
    assert runner.busy and job.status == "in_progress"
    assert runner_id in dt._holding
