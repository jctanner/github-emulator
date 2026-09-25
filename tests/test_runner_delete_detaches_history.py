"""Deleting a runner detaches the jobs that record having used it.

`workflow_jobs.runner_id` is a foreign key to `runners.id` and SQLite does not
enforce it, so deleting a referenced runner silently left rows pointing at
nothing. The practical consequence was that the dead registrations a restarted
stack accumulates could not be cleared: on one stack, 62 of 114 runners were
undeletable because some completed job still pointed at them.

Jobs keep `runner_name`, which is denormalised, so the history still says which
runner ran what. A runner still executing something is refused instead.
"""

import pytest
from sqlalchemy import select

from tests.conftest import auth_headers
from app.models.actions import Runner, WorkflowJob

API = "/api/v3"


async def _repo_with_runner(client, token, db, name, *, job_status="completed"):
    """A repo, a repo-scoped runner, and one job recording that runner."""
    created = await client.post(
        f"{API}/user/repos", json={"name": name}, headers=auth_headers(token)
    )
    assert created.status_code in (200, 201), created.text
    repo_id = created.json()["id"]

    runner = Runner(name="dead-runner", os="linux", status="online",
                    labels=["self-hosted"], busy=False, repo_id=repo_id)
    db.add(runner)
    await db.commit()
    await db.refresh(runner)

    job = WorkflowJob(run_id=1, name="Route", status=job_status,
                      conclusion="success" if job_status == "completed" else None,
                      runner_id=runner.id, runner_name="dead-runner")
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return runner.id, job.id


@pytest.mark.asyncio
async def test_a_runner_with_completed_job_history_can_be_deleted(
    client, test_user, test_token, db_session
):
    """The case that was impossible before: a referenced runner."""
    runner_id, job_id = await _repo_with_runner(
        client, test_token, db_session, "rd-hist")

    resp = await client.delete(
        f"{API}/repos/testuser/rd-hist/actions/runners/{runner_id}",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 204, resp.text

    assert (await db_session.execute(
        select(Runner).where(Runner.id == runner_id))).scalar_one_or_none() is None

    job = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.id == job_id))).scalar_one()
    assert job.runner_id is None, "job still points at a deleted runner"
    assert job.runner_name == "dead-runner", "history lost which runner ran it"


@pytest.mark.asyncio
async def test_a_runner_running_a_job_is_refused(
    client, test_user, test_token, db_session
):
    """Detaching a live job would strand it: requeueing finds work by joining
    jobs to their runner, so a detached in-progress job is recovered by nothing."""
    runner_id, job_id = await _repo_with_runner(
        client, test_token, db_session, "rd-live", job_status="in_progress")

    resp = await client.delete(
        f"{API}/repos/testuser/rd-live/actions/runners/{runner_id}",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 422, resp.text
    assert "is running job" in str(resp.json())

    assert (await db_session.execute(
        select(Runner).where(Runner.id == runner_id))).scalar_one_or_none() is not None
    job = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.id == job_id))).scalar_one()
    assert job.runner_id == runner_id, "a refused delete must change nothing"


@pytest.mark.asyncio
async def test_no_dangling_references_remain_after_deletion(
    client, test_user, test_token, db_session
):
    """The property that matters, stated directly."""
    runner_id, _ = await _repo_with_runner(
        client, test_token, db_session, "rd-dangle")
    await client.delete(
        f"{API}/repos/testuser/rd-dangle/actions/runners/{runner_id}",
        headers=auth_headers(test_token),
    )
    dangling = (await db_session.execute(select(WorkflowJob).where(
        WorkflowJob.runner_id.is_not(None),
        WorkflowJob.runner_id.not_in(select(Runner.id)),
    ))).scalars().all()
    assert dangling == [], f"{len(dangling)} job(s) point at a missing runner"


@pytest.mark.asyncio
async def test_a_runner_with_open_sessions_can_be_deleted(
    client, test_user, test_token, db_session
):
    """runner_sessions.runner_id is NOT NULL, so it cannot be detached.

    The first version of the delete handled only workflow_jobs and returned 500
    for any runner that had ever opened a session — SQLAlchemy tried to null a
    non-nullable column on cascade. Observed on a real stack: one runner with
    four sessions was the single row that would not clear.
    """
    from app.models.actions import RunnerSession

    runner_id, job_id = await _repo_with_runner(
        client, test_token, db_session, "rd-sess")
    for n in range(4):
        db_session.add(RunnerSession(runner_id=runner_id, session_id=f"sess-{runner_id}-{n}"))
    await db_session.commit()

    resp = await client.delete(
        f"{API}/repos/testuser/rd-sess/actions/runners/{runner_id}",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 204, resp.text

    assert (await db_session.execute(
        select(Runner).where(Runner.id == runner_id))).scalar_one_or_none() is None
    left = (await db_session.execute(
        select(RunnerSession).where(RunnerSession.runner_id == runner_id))).scalars().all()
    assert left == [], "sessions outlived their runner"
    job = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.id == job_id))).scalar_one()
    assert job.runner_id is None and job.runner_name == "dead-runner"
