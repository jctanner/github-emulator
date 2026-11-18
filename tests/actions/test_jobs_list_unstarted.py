"""The jobs list shows a job once it is queued, not while it waits on needs.

Probed on GitHub (bt-f3): while job a ran, GET /runs/{id}/jobs listed only a
(total_count 1); b and c, which need a, appeared once they started. The
emulator listed all three, b and c as "waiting", so a stage that had not
started looked like one in the run.
"""

import pytest

from app.models.actions import Workflow, WorkflowJob, WorkflowRun
from tests.conftest import API, auth_headers


@pytest.mark.asyncio
async def test_a_job_waiting_on_needs_is_not_listed_until_queued(
    client, db_session, test_user, test_token, test_repo_with_init
):
    _owner, _repo, repo = test_repo_with_init
    workflow = Workflow(repo_id=repo["id"], name="Needs", path=".github/workflows/needs.yml")
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id, repo_id=repo["id"], head_sha="abc", head_branch="main",
        event="workflow_dispatch", status="in_progress", run_number=1, actor_id=test_user.id,
    )
    db_session.add(run)
    await db_session.flush()
    a = WorkflowJob(run_id=run.id, name="a", status="in_progress")
    b = WorkflowJob(run_id=run.id, name="b", status="waiting", needs=["a"])
    db_session.add_all([a, b])
    await db_session.commit()
    url = f"{API}/repos/testuser/init-repo/actions/runs/{run.id}/jobs"

    listed = (await client.get(url, headers=auth_headers(test_token))).json()
    assert listed["total_count"] == 1
    assert [job["name"] for job in listed["jobs"]] == ["a"]

    b.status = "queued"
    await db_session.commit()
    listed = (await client.get(url, headers=auth_headers(test_token))).json()
    assert sorted(job["name"] for job in listed["jobs"]) == ["a", "b"]
