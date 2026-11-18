"""An upstream runner is told when the job it is running gets cancelled.

A newer job in the same `concurrency:` group with `cancel-in-progress: true`
cancels the running one. The emulator recorded that in the database but never
told the runner, so the cancelled job ran to its end, and a superseded stage
ran twice.
"""

import json

import pytest
from sqlalchemy import select

from app.api import actions_distributed_task as dt
from app.api.actions_runner_protocol import _workflow_guid
from app.models.actions import Runner, WorkflowJob
from tests.actions.test_uses_steps import _claim_job, uses_workflow  # noqa: F401


@pytest.fixture(autouse=True)
def _fresh_runner_state():
    # Module state keyed by ids; each test's database starts ids at 1.
    dt._holding.clear()
    dt._cancellations_sent.clear()
    yield
    dt._holding.clear()
    dt._cancellations_sent.clear()


async def _job_and_runner(db_session):
    db_session.expire_all()
    job = (await db_session.execute(select(WorkflowJob))).scalars().first()
    runner = (await db_session.execute(select(Runner).where(Runner.id == job.runner_id))).scalar_one()
    return job, runner


async def _cancel(db_session, job):
    # What the scheduler does when a newer job supersedes this one.
    job.status = "completed"
    job.conclusion = "cancelled"
    await db_session.commit()


@pytest.mark.asyncio
async def test_the_runner_gets_a_cancellation_for_its_cancelled_job(
    client, db_session, uses_workflow, test_token  # noqa: F811
):
    await _claim_job(client, test_token)
    job, runner = await _job_and_runner(db_session)
    assert runner.busy
    await _cancel(db_session, job)

    message = await dt._pending_cancellation(runner, db_session)
    assert message["messageType"] == "JobCancellation"
    body = json.loads(message["body"])
    assert body == {"jobId": _workflow_guid("job", job.id), "timeout": "00:05:00"}

    # Once per job.
    assert await dt._pending_cancellation(runner, db_session) is None


@pytest.mark.asyncio
async def test_no_cancellation_while_the_job_still_runs(
    client, db_session, uses_workflow, test_token  # noqa: F811
):
    await _claim_job(client, test_token)
    _job, runner = await _job_and_runner(db_session)
    assert await dt._pending_cancellation(runner, db_session) is None


@pytest.mark.asyncio
async def test_the_runners_report_keeps_cancelled_and_frees_the_runner(
    client, db_session, uses_workflow, test_token  # noqa: F811
):
    await _claim_job(client, test_token)
    job, runner = await _job_and_runner(db_session)
    await _cancel(db_session, job)

    await dt._complete_job(job, runner, "Succeeded", db_session)
    await db_session.commit()
    job, runner = await _job_and_runner(db_session)
    assert (job.status, job.conclusion) == ("completed", "cancelled")
    assert not runner.busy
    assert await dt._pending_cancellation(runner, db_session) is None
