"""Loading a workflow, a run or a job must not load the repository's history.

Workflow.runs and WorkflowRun.jobs were lazy="selectin". Any query that
touched one of the three models then pulled every run of the workflow and
every job of every run - the whole Actions history of the repository, with
each job's steps JSON decoded - into the session. Event dispatch loads a
repository's workflows to match triggers, so each issue, pull request and
comment paid that in full: 17 to 47 seconds and ~200 MiB on a target with
1,600 runs, and three OOM kills at the 1.5 GiB limit.

These pin the fix: the two collections are lazy="raise", so nothing loads
them by accident, and reading one is an error rather than a silent load.
"""

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import InvalidRequestError

from app.models.actions import Workflow, WorkflowJob, WorkflowRun
from app.models.repository import Repository
from app.services.workflow_service import create_workflow_run
from tests.conftest import API, auth_headers


@pytest.fixture
async def history(client, db_session, test_user, test_token):
    resp = await client.post(
        f"{API}/user/repos", json={"name": "history-repo"}, headers=auth_headers(test_token)
    )
    assert resp.status_code == 201
    repo = (await db_session.execute(
        select(Repository).where(Repository.full_name == "testuser/history-repo")
    )).scalar_one()
    workflow = Workflow(repo_id=repo.id, name="CI", path=".github/workflows/ci.yml")
    db_session.add(workflow)
    await db_session.flush()
    yaml = {
        "name": "CI", "on": ["push"],
        "jobs": {"a": {"runs-on": ["self-hosted"], "steps": [{"run": "true"}]},
                 "b": {"runs-on": ["self-hosted"], "needs": ["a"], "steps": [{"run": "true"}]}},
    }
    runs = []
    for i in range(3):
        runs.append(await create_workflow_run(
            db_session, workflow, yaml, event="push", payload={"ref": "refs/heads/main"},
            actor=test_user, head_sha=f"sha{i}", head_branch="main",
        ))
    await db_session.commit()
    db_session.expunge_all()
    return workflow.id, [r.id for r in runs]


async def test_loading_a_workflow_does_not_load_its_runs(db_session, history):
    workflow_id, _ = history
    workflow = (await db_session.execute(
        select(Workflow).where(Workflow.id == workflow_id)
    )).scalar_one()
    assert "runs" in inspect(workflow).unloaded
    with pytest.raises(InvalidRequestError):
        workflow.runs  # noqa: B018 - the access is the assertion


async def test_loading_a_run_does_not_load_its_jobs_or_sibling_runs(db_session, history):
    _, run_ids = history
    run = (await db_session.execute(
        select(WorkflowRun).where(WorkflowRun.id == run_ids[0])
    )).scalar_one()
    assert "jobs" in inspect(run).unloaded
    # The many-to-one to the workflow may load; the workflow's history must not.
    assert run.workflow is not None
    assert "runs" in inspect(run.workflow).unloaded


async def test_loading_a_job_does_not_chain_into_history(db_session, history):
    _, run_ids = history
    job = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run_ids[1])
    )).scalars().first()
    assert job is not None
    assert job.run is not None
    assert "jobs" in inspect(job.run).unloaded
    assert "runs" in inspect(job.run.workflow).unloaded
