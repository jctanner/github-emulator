"""A cancellation reaches the runner even when it lands mid-poll or after a
late report of the runner's previous job.

Seen with four real runners: a runner finished job A and took job B in the
same second; A's finish request landed after the claim and freed the runner,
so B's later cancellation (superseded under ``cancel-in-progress``) was never
sent, and both stages ran to their end.
"""

import json

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
async def test_a_cancellation_committed_elsewhere_is_seen_by_a_session_that_loaded_the_job(
    client, db_session, db_engine, uses_workflow, test_token  # noqa: F811
):
    body, job, runner = await _claimed(client, db_session, test_token)
    assert job.conclusion is None  # loaded into the polling session

    # Another request (the superseding job's promotion) cancels it.
    other = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with other() as session:
        await session.execute(
            update(WorkflowJob).where(WorkflowJob.id == job.id).values(status="completed", conclusion="cancelled")
        )
        await session.commit()

    message = await dt._pending_cancellation(runner, db_session)
    assert message is not None and message["messageType"] == "JobCancellation"
    assert json.loads(message["body"])["jobId"] == body["jobId"]


@pytest.mark.asyncio
async def test_a_late_report_of_the_previous_job_leaves_the_runner_on_its_new_job(
    client, db_session, uses_workflow, test_token  # noqa: F811
):
    _body, job, runner = await _claimed(client, db_session, test_token)
    runner_id, next_job_id = runner.id, job.id + 1000
    # The runner has since taken another job.
    dt._holding[runner_id] = (next_job_id, "next-job-guid")

    await dt._complete_job(job, runner, "Succeeded", db_session)
    await db_session.commit()

    db_session.expire_all()
    runner = (await db_session.execute(select(Runner).where(Runner.id == runner_id))).scalar_one()
    assert runner.busy
    assert dt._holding[runner_id][0] == next_job_id


@pytest.mark.asyncio
async def test_the_report_of_the_held_job_frees_the_runner(
    client, db_session, uses_workflow, test_token  # noqa: F811
):
    _body, job, runner = await _claimed(client, db_session, test_token)
    runner_id = runner.id
    assert dt._holding[runner_id][0] == job.id

    await dt._complete_job(job, runner, "Succeeded", db_session)
    await db_session.commit()

    db_session.expire_all()
    runner = (await db_session.execute(select(Runner).where(Runner.id == runner_id))).scalar_one()
    assert not runner.busy
    assert runner_id not in dt._holding
