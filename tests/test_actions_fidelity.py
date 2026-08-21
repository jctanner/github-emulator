"""Artifacts, permissions metadata, and cancellation contract coverage."""

import pytest

from app.models.actions import Workflow, WorkflowJob, WorkflowRun
from app.services.workflow_service import create_workflow_run
from tests.conftest import API, auth_headers


@pytest.mark.asyncio
async def test_artifact_and_permissions_metadata(client, db_session, test_user, test_token, test_repo_with_init):
    _owner, _repo, repo = test_repo_with_init
    workflow = Workflow(repo_id=repo["id"], name="Fidelity", path=".github/workflows/fidelity.yml")
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(workflow_id=workflow.id, repo_id=repo["id"], head_sha="abc", head_branch="main", event="workflow_dispatch", status="queued", run_number=1, actor_id=test_user.id)
    db_session.add(run)
    await db_session.flush()
    job = WorkflowJob(run_id=run.id, name="fidelity", status="queued", permissions={"contents": "read", "issues": "write"})
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(run)
    await db_session.refresh(job)

    jobs = await client.get(f"{API}/repos/testuser/init-repo/actions/runs/{run.id}/jobs", headers=auth_headers(test_token))
    assert jobs.status_code == 200
    assert jobs.json()["jobs"][0]["permissions"] == {"contents": "read", "issues": "write"}

    uploaded = await client.post(
        f"{API}/repos/testuser/init-repo/actions/runs/{run.id}/artifacts",
        headers=auth_headers(test_token),
        json={"name": "m8-evidence", "files": {"result.json": "{}"}},
    )
    assert uploaded.status_code == 201
    artifact_id = uploaded.json()["id"]
    listed = await client.get(f"{API}/repos/testuser/init-repo/actions/runs/{run.id}/artifacts", headers=auth_headers(test_token))
    assert listed.json()["total_count"] == 1
    fetched = await client.get(f"{API}/repos/testuser/init-repo/actions/artifacts/{artifact_id}", headers=auth_headers(test_token))
    assert fetched.json()["files"] == {"result.json": "{}"}
    assert (await client.delete(f"{API}/repos/testuser/init-repo/actions/artifacts/{artifact_id}", headers=auth_headers(test_token))).status_code == 204


@pytest.mark.asyncio
async def test_cancel_queued_run_marks_job_cancelled(client, db_session, test_user, test_token, test_repo_with_init):
    _owner, _repo, repo = test_repo_with_init
    workflow = Workflow(repo_id=repo["id"], name="Cancel", path=".github/workflows/cancel.yml")
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(workflow_id=workflow.id, repo_id=repo["id"], head_sha="def", head_branch="main", event="workflow_dispatch", status="queued", run_number=1, actor_id=test_user.id)
    db_session.add(run)
    await db_session.flush()
    db_session.add(WorkflowJob(run_id=run.id, name="pending", status="queued"))
    await db_session.commit()
    response = await client.post(f"{API}/repos/testuser/init-repo/actions/runs/{run.id}/cancel", headers=auth_headers(test_token))
    assert response.status_code == 202
    run_response = await client.get(f"{API}/repos/testuser/init-repo/actions/runs/{run.id}", headers=auth_headers(test_token))
    assert run_response.json()["conclusion"] == "cancelled"


@pytest.mark.asyncio
async def test_concurrency_group_cancels_previous_run(db_session, test_user, test_repo_with_init):
    _owner, _repo, repo = test_repo_with_init
    workflow = Workflow(repo_id=repo["id"], name="Concurrency", path=".github/workflows/concurrency.yml")
    db_session.add(workflow)
    await db_session.flush()
    yaml = {"concurrency": {"group": "m8-main", "cancel-in-progress": True}, "jobs": {"pending": {"runs-on": ["self-hosted"], "steps": [{"run": "true"}]}}}
    first = await create_workflow_run(db_session, workflow, yaml, "workflow_dispatch", {"repository": {"full_name": "testuser/init-repo"}}, test_user, "abc", "main")
    second = await create_workflow_run(db_session, workflow, yaml, "workflow_dispatch", {"repository": {"full_name": "testuser/init-repo"}}, test_user, "def", "main")
    assert first.conclusion == "cancelled"
    assert second.concurrency_group == "m8-main"
