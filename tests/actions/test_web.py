"""Tests for Actions web UI visibility."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.actions import Runner, Workflow, WorkflowJob, WorkflowRun
from app.models.repository import Repository
from tests.conftest import auth_headers

API = "/api/v3"


@pytest.fixture
async def web_actions_repo(client, db_session, test_user, test_token):
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "web-actions-repo"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    repo = (await db_session.execute(
        select(Repository).where(Repository.full_name == "testuser/web-actions-repo")
    )).scalar_one()

    workflow = Workflow(repo_id=repo.id, name="CI", path=".github/workflows/ci.yml")
    db_session.add(workflow)
    await db_session.flush()

    run = WorkflowRun(
        workflow_id=workflow.id,
        repo_id=repo.id,
        head_sha="def456",
        head_branch="main",
        event="push",
        status="in_progress",
        conclusion=None,
        run_number=7,
        run_attempt=1,
        actor_id=test_user.id,
    )
    db_session.add(run)
    await db_session.flush()

    runner = Runner(
        name="web-runner",
        os="linux",
        status="busy",
        labels=["self-hosted", "linux"],
        busy=True,
        repo_id=repo.id,
        last_heartbeat=datetime.now(timezone.utc),
    )
    db_session.add(runner)
    await db_session.flush()

    job = WorkflowJob(
        run_id=run.id,
        name="test",
        workflow_name="CI",
        status="in_progress",
        conclusion=None,
        started_at=datetime.now(timezone.utc),
        steps=[
            {"number": 1, "name": "Checkout", "status": "completed", "conclusion": "success"},
            {"number": 2, "name": "Pytest", "status": "in_progress", "conclusion": None},
        ],
        runner_name=runner.name,
        runner_id=runner.id,
        labels=["self-hosted", "linux"],
    )
    db_session.add(job)
    await db_session.commit()
    return repo, workflow, run, job, runner


@pytest.mark.asyncio
async def test_actions_tab_on_repo_page(client, web_actions_repo):
    resp = await client.get("/ui/testuser/web-actions-repo")
    assert resp.status_code == 200
    assert "Actions" in resp.text
    assert "/ui/testuser/web-actions-repo/actions" in resp.text


@pytest.mark.asyncio
async def test_actions_list_page(client, web_actions_repo):
    resp = await client.get("/ui/testuser/web-actions-repo/actions")
    assert resp.status_code == 200
    assert "Workflows" in resp.text
    assert "Recent Runs" in resp.text
    assert "CI #7" in resp.text
    assert "1 runner" in resp.text


@pytest.mark.asyncio
async def test_actions_run_detail_page(client, web_actions_repo):
    _repo, _workflow, run, _job, _runner = web_actions_repo
    resp = await client.get(f"/ui/testuser/web-actions-repo/actions/runs/{run.id}")
    assert resp.status_code == 200
    assert "Run metadata" in resp.text
    assert "def456" in resp.text
    assert "test" in resp.text
    assert "web-runner" in resp.text


@pytest.mark.asyncio
async def test_actions_job_detail_page(client, web_actions_repo):
    _repo, _workflow, _run, job, _runner = web_actions_repo
    resp = await client.get(f"/ui/testuser/web-actions-repo/actions/jobs/{job.id}")
    assert resp.status_code == 200
    assert "Job metadata" in resp.text
    assert "Checkout" in resp.text
    assert "Pytest" in resp.text
    assert "No logs have been uploaded" in resp.text


@pytest.mark.asyncio
async def test_actions_job_live_endpoint_returns_state_and_logs(client, web_actions_repo):
    _repo, _workflow, _run, job, _runner = web_actions_repo
    resp = await client.get(f"/ui/testuser/web-actions-repo/actions/jobs/{job.id}/live")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "in_progress"
    assert data["logs"] == ""
    assert data["steps"][1]["status"] == "in_progress"


@pytest.mark.asyncio
async def test_actions_runners_page(client, web_actions_repo):
    resp = await client.get("/ui/testuser/web-actions-repo/actions/runners")
    assert resp.status_code == 200
    assert "Repository runners" in resp.text
    assert "web-runner" in resp.text
    assert "busy" in resp.text
    assert "self-hosted" in resp.text


@pytest.mark.asyncio
async def test_actions_empty_states(client, test_token):
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "empty-actions-repo"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201

    resp = await client.get("/ui/testuser/empty-actions-repo/actions")
    assert resp.status_code == 200
    assert "No workflows have been detected" in resp.text
    assert "No workflow runs yet" in resp.text

    resp = await client.get("/ui/testuser/empty-actions-repo/actions/runners")
    assert resp.status_code == 200
    assert "No runners registered" in resp.text


@pytest.mark.asyncio
async def test_private_actions_page_hidden_without_session(client, test_token):
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "private-web-actions-repo", "private": True},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201

    resp = await client.get("/ui/testuser/private-web-actions-repo/actions")
    assert resp.status_code == 404
