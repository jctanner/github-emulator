"""Tests for GitHub Actions visibility API endpoints."""

import os
from datetime import datetime, timezone

import pytest

from app.config import settings
from app.models.actions import Runner, Workflow, WorkflowJob, WorkflowRun
from tests.conftest import auth_headers

API = "/api/v3"


@pytest.fixture
async def actions_repo(client, db_session, test_user, test_token):
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "actions-api-repo"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201

    from app.models.repository import Repository
    from sqlalchemy import select

    repo = (await db_session.execute(
        select(Repository).where(Repository.full_name == "testuser/actions-api-repo")
    )).scalar_one()

    workflow = Workflow(
        repo_id=repo.id,
        name="CI",
        path=".github/workflows/ci.yml",
    )
    db_session.add(workflow)
    await db_session.flush()

    run = WorkflowRun(
        workflow_id=workflow.id,
        repo_id=repo.id,
        head_sha="abc123",
        head_branch="main",
        event="push",
        status="completed",
        conclusion="success",
        run_number=1,
        run_attempt=1,
        actor_id=test_user.id,
    )
    db_session.add(run)
    await db_session.flush()

    runner = Runner(
        name="runner-1",
        os="linux",
        status="online",
        labels=["self-hosted", "linux"],
        busy=False,
        repo_id=repo.id,
        last_heartbeat=datetime.now(timezone.utc),
    )
    db_session.add(runner)
    await db_session.flush()

    job = WorkflowJob(
        run_id=run.id,
        name="build",
        workflow_name="CI",
        status="completed",
        conclusion="success",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        steps=[
            {"number": 1, "name": "Checkout", "status": "completed", "conclusion": "success"},
            {"number": 2, "name": "Test", "status": "completed", "conclusion": "success"},
        ],
        runner_name=runner.name,
        runner_id=runner.id,
        labels=["self-hosted", "linux"],
        run_attempt=1,
    )
    db_session.add(job)
    await db_session.commit()

    log_dir = os.path.join(settings.DATA_DIR, "logs", "jobs")
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, f"{job.id}.log"), "w", encoding="utf-8") as f:
        f.write("job log line\n")

    return repo, workflow, run, job, runner


@pytest.mark.asyncio
async def test_actions_list_workflows_and_runs(client, actions_repo):
    resp = await client.get(f"{API}/repos/testuser/actions-api-repo/actions/workflows")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 1
    assert data["workflows"][0]["name"] == "CI"
    assert data["workflows"][0]["state"] == "disabled_manually"

    resp = await client.get(f"{API}/repos/testuser/actions-api-repo/actions/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 1
    assert data["workflow_runs"][0]["status"] == "completed"
    assert data["workflow_runs"][0]["conclusion"] == "success"
    assert data["workflow_runs"][0]["actor"]["login"] == "testuser"


@pytest.mark.asyncio
async def test_actions_job_detail_and_logs(client, actions_repo):
    _repo, _workflow, run, job, _runner = actions_repo

    resp = await client.get(
        f"{API}/repos/testuser/actions-api-repo/actions/runs/{run.id}/jobs"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 1
    assert data["jobs"][0]["name"] == "build"
    assert data["jobs"][0]["runner_name"] == "runner-1"
    assert data["jobs"][0]["steps"][1]["name"] == "Test"

    resp = await client.get(
        f"{API}/repos/testuser/actions-api-repo/actions/jobs/{job.id}"
    )
    assert resp.status_code == 200
    assert resp.json()["logs_url"].endswith(f"/actions/jobs/{job.id}/logs")

    resp = await client.get(
        f"{API}/repos/testuser/actions-api-repo/actions/jobs/{job.id}/logs"
    )
    assert resp.status_code == 200
    assert "job log line" in resp.text


@pytest.mark.asyncio
async def test_actions_job_404_scoped_to_repo(client, actions_repo, test_token):
    _repo, _workflow, _run, job, _runner = actions_repo
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "other-actions-repo"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201

    resp = await client.get(
        f"{API}/repos/testuser/other-actions-repo/actions/jobs/{job.id}"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_private_actions_require_access(client, db_session, test_user, test_token):
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "private-actions-repo", "private": True},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201

    from app.models.repository import Repository
    from sqlalchemy import select

    repo = (await db_session.execute(
        select(Repository).where(Repository.full_name == "testuser/private-actions-repo")
    )).scalar_one()
    workflow = Workflow(repo_id=repo.id, name="Private CI", path=".github/workflows/ci.yml")
    db_session.add(workflow)
    await db_session.commit()

    resp = await client.get(
        f"{API}/repos/testuser/private-actions-repo/actions/workflows"
    )
    assert resp.status_code == 404

    resp = await client.get(
        f"{API}/repos/testuser/private-actions-repo/actions/workflows",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    assert resp.json()["total_count"] == 1
