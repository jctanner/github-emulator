"""Artifacts, permissions metadata, and cancellation contract coverage."""

import io
import zipfile

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
    # GitHub puts the archive format in the download URL, not the artifact name.
    assert uploaded.json()["archive_download_url"].endswith(f"/actions/artifacts/{artifact_id}/zip")
    listed = await client.get(f"{API}/repos/testuser/init-repo/actions/runs/{run.id}/artifacts", headers=auth_headers(test_token))
    assert listed.json()["total_count"] == 1

    # The row indexes the upload; the bytes come back from disk.
    fetched = await client.get(f"{API}/repos/testuser/init-repo/actions/artifacts/{artifact_id}", headers=auth_headers(test_token))
    assert fetched.json()["files"] == {"result.json": 2}
    assert fetched.json()["size_in_bytes"] == 2

    archive = await client.get(f"{API}/repos/testuser/init-repo/actions/artifacts/{artifact_id}/zip", headers=auth_headers(test_token))
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        assert zf.namelist() == ["result.json"]
        assert zf.read("result.json") == b"{}"

    single = await client.get(f"{API}/repos/testuser/init-repo/actions/artifacts/{artifact_id}/files/result.json", headers=auth_headers(test_token))
    assert single.status_code == 200
    assert single.content == b"{}"

    assert (await client.get(f"{API}/repos/testuser/init-repo/actions/artifacts/{artifact_id}/tar", headers=auth_headers(test_token))).status_code == 404

    assert (await client.delete(f"{API}/repos/testuser/init-repo/actions/artifacts/{artifact_id}", headers=auth_headers(test_token))).status_code == 204
    assert (await client.get(f"{API}/repos/testuser/init-repo/actions/artifacts/{artifact_id}/zip", headers=auth_headers(test_token))).status_code == 404


@pytest.mark.asyncio
async def test_artifact_zip_upload_round_trips_binary(client, db_session, test_user, test_token, test_repo_with_init):
    """A zip upload carries binary content and directory structure intact.

    This is the path a runner uses for a real upload-artifact step, and the
    reason artifacts are stored as files rather than in a JSON column.
    """
    _owner, _repo, repo = test_repo_with_init
    workflow = Workflow(repo_id=repo["id"], name="Artifacts", path=".github/workflows/artifacts.yml")
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(workflow_id=workflow.id, repo_id=repo["id"], head_sha="abc", head_branch="main", event="workflow_dispatch", status="queued", run_number=1, actor_id=test_user.id)
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    blob = bytes(range(256))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("logs/openshell.log", "line one\nline two\n")
        zf.writestr("nested/deep/blob.bin", blob)

    uploaded = await client.post(
        f"{API}/repos/testuser/init-repo/actions/runs/{run.id}/artifacts?name=evidence",
        headers={**auth_headers(test_token), "Content-Type": "application/zip"},
        content=buffer.getvalue(),
    )
    assert uploaded.status_code == 201, uploaded.text
    artifact_id = uploaded.json()["id"]

    fetched = await client.get(f"{API}/repos/testuser/init-repo/actions/artifacts/{artifact_id}", headers=auth_headers(test_token))
    assert fetched.json()["files"] == {"logs/openshell.log": 18, "nested/deep/blob.bin": 256}

    single = await client.get(f"{API}/repos/testuser/init-repo/actions/artifacts/{artifact_id}/files/nested/deep/blob.bin", headers=auth_headers(test_token))
    assert single.status_code == 200
    assert single.content == blob


@pytest.mark.asyncio
async def test_artifact_upload_rejects_path_escape(client, db_session, test_user, test_token, test_repo_with_init):
    """A member path that escapes the artifact directory is refused.

    Member paths come from whoever uploaded the artifact, so a stored "../"
    would let an upload write over unrelated emulator data.
    """
    _owner, _repo, repo = test_repo_with_init
    workflow = Workflow(repo_id=repo["id"], name="Escape", path=".github/workflows/escape.yml")
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(workflow_id=workflow.id, repo_id=repo["id"], head_sha="abc", head_branch="main", event="workflow_dispatch", status="queued", run_number=1, actor_id=test_user.id)
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    escaped = await client.post(
        f"{API}/repos/testuser/init-repo/actions/runs/{run.id}/artifacts",
        headers=auth_headers(test_token),
        json={"name": "bad", "files": {"../../escaped.txt": "nope"}},
    )
    assert escaped.status_code == 400

    listed = await client.get(f"{API}/repos/testuser/init-repo/actions/runs/{run.id}/artifacts", headers=auth_headers(test_token))
    assert listed.json()["total_count"] == 0


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
async def test_cancel_active_run_marks_job_cancelled(client, db_session, test_user, test_token, test_repo_with_init):
    _owner, _repo, repo = test_repo_with_init
    workflow = Workflow(repo_id=repo["id"], name="Cancel active", path=".github/workflows/cancel-active.yml")
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        repo_id=repo["id"],
        head_sha="active",
        head_branch="main",
        event="workflow_dispatch",
        status="in_progress",
        run_number=1,
        actor_id=test_user.id,
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(WorkflowJob(run_id=run.id, name="active", status="in_progress"))
    await db_session.commit()

    response = await client.post(f"{API}/repos/testuser/init-repo/actions/runs/{run.id}/cancel", headers=auth_headers(test_token))
    assert response.status_code == 202
    jobs = await client.get(f"{API}/repos/testuser/init-repo/actions/runs/{run.id}/jobs", headers=auth_headers(test_token))
    assert jobs.json()["jobs"][0]["status"] == "completed"
    assert jobs.json()["jobs"][0]["conclusion"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_already_cancelled_run_sweeps_unfinished_job(client, db_session, test_user, test_token, test_repo_with_init):
    _owner, _repo, repo = test_repo_with_init
    workflow = Workflow(repo_id=repo["id"], name="Cancel stale", path=".github/workflows/cancel-stale.yml")
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        repo_id=repo["id"],
        head_sha="stale",
        head_branch="main",
        event="workflow_dispatch",
        status="completed",
        conclusion="cancelled",
        run_number=1,
        actor_id=test_user.id,
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(WorkflowJob(run_id=run.id, name="orphan", status="in_progress"))
    await db_session.commit()

    response = await client.post(f"{API}/repos/testuser/init-repo/actions/runs/{run.id}/cancel", headers=auth_headers(test_token))
    assert response.status_code == 202
    jobs = await client.get(f"{API}/repos/testuser/init-repo/actions/runs/{run.id}/jobs", headers=auth_headers(test_token))
    assert jobs.json()["jobs"][0]["status"] == "completed"
    assert jobs.json()["jobs"][0]["conclusion"] == "cancelled"


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
