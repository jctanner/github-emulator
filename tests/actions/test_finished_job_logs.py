"""GET /actions/jobs/{id}/logs serves a finished job's uploaded step logs.

The runner sends each line twice: live, through the console feed, which it
cuts at 1024 characters, and whole, in the step logs it uploads as each step
ends. GitHub serves a finished job's log from the uploaded step logs; while
the job runs, the live feed is all there is.
"""

import os

import pytest
from sqlalchemy import select

from app.api.actions_distributed_task import _job_log_path, _step_log_dir
from app.models.actions import Workflow, WorkflowJob, WorkflowRun
from app.models.repository import Repository
from tests.conftest import API, auth_headers

LONG = "x" * 1500


async def _job(client, db_session, test_user, test_token) -> WorkflowJob:
    resp = await client.post(f"{API}/user/repos", json={"name": "logs"}, headers=auth_headers(test_token))
    assert resp.status_code == 201, resp.text
    repo = (await db_session.execute(select(Repository).where(Repository.name == "logs"))).scalar_one()
    workflow = Workflow(repo_id=repo.id, name="W", path=".github/workflows/w.yml")
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id, repo_id=repo.id, head_sha="abc", head_branch="main",
        event="push", run_number=1, actor_id=test_user.id,
    )
    db_session.add(run)
    await db_session.flush()
    job = WorkflowJob(run_id=run.id, name="j", status="in_progress")
    db_session.add(job)
    await db_session.commit()

    # The live feed holds the line cut short; the uploaded step logs hold it
    # whole, one file per log id, with id 10 sorting after id 2.
    os.makedirs(os.path.dirname(_job_log_path(job.id)), exist_ok=True)
    with open(_job_log_path(job.id), "w") as f:
        f.write("first\n" + LONG[:1024] + "\n")
    steps = _step_log_dir(job.id)
    os.makedirs(steps, exist_ok=True)
    with open(os.path.join(steps, "2.log"), "w") as f:
        f.write("first")
    with open(os.path.join(steps, "10.log"), "w") as f:
        f.write(LONG + "\n")
    open(os.path.join(steps, "11.log"), "w").close()  # reserved, never written
    return job


@pytest.mark.asyncio
async def test_a_finished_job_serves_its_uploaded_step_logs(client, db_session, test_user, test_token):
    job = await _job(client, db_session, test_user, test_token)
    job.status = "completed"
    await db_session.commit()

    resp = await client.get(f"{API}/repos/testuser/logs/actions/jobs/{job.id}/logs", headers=auth_headers(test_token))
    assert resp.status_code == 200, resp.text
    assert resp.text == "first\n" + LONG + "\n"


@pytest.mark.asyncio
async def test_a_running_job_serves_the_live_feed(client, db_session, test_user, test_token):
    job = await _job(client, db_session, test_user, test_token)

    resp = await client.get(f"{API}/repos/testuser/logs/actions/jobs/{job.id}/logs", headers=auth_headers(test_token))
    assert resp.status_code == 200, resp.text
    assert resp.text == "first\n" + LONG[:1024] + "\n"
