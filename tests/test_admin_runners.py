"""Admin UI coverage for Actions runner registration visibility."""

from datetime import datetime, timezone

import pytest

from app.admin.routes import _sign_session
from app.models.actions import Runner, Workflow, WorkflowJob, WorkflowRun


def admin_cookies() -> dict[str, str]:
    return {"admin_session": _sign_session("admin")}


@pytest.mark.asyncio
async def test_admin_runners_page_requires_admin_session(client):
    response = await client.get("/ui-legacy/_admin/runners", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/ui-legacy/_admin/login"


@pytest.mark.asyncio
async def test_admin_runners_page_shows_scope_health_labels_and_current_job(
    client, db_session, test_user, test_repo_with_init
):
    _owner, _repo_name, repository = test_repo_with_init
    workflow = Workflow(
        repo_id=repository["id"],
        name="Fullsend",
        path=".github/workflows/fullsend.yaml",
    )
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        repo_id=repository["id"],
        head_sha="abc123",
        head_branch="main",
        event="issues",
        status="in_progress",
        run_number=1,
        actor_id=test_user.id,
    )
    db_session.add(run)
    await db_session.flush()
    runner = Runner(
        name="fullsend-router",
        os="linux",
        status="online",
        busy=True,
        labels=["self-hosted", "linux", "fullsend"],
        repo_id=repository["id"],
        token_hash="must-not-render",
        last_heartbeat=datetime.now(timezone.utc),
    )
    db_session.add(runner)
    await db_session.flush()
    db_session.add(
        Runner(
            name="site-router",
            os="linux",
            status="online",
            busy=False,
            labels=["self-hosted", "linux", "fullsend-router"],
            repo_id=None,
            org_id=None,
            token_hash="site-token-must-not-render",
            last_heartbeat=datetime.now(timezone.utc),
        )
    )
    job = WorkflowJob(
        run_id=run.id,
        name="Route Fullsend event",
        status="in_progress",
        runner_id=runner.id,
        runner_name=runner.name,
        labels=["self-hosted", "linux", "fullsend"],
    )
    db_session.add(job)
    await db_session.commit()

    response = await client.get("/ui-legacy/_admin/runners", cookies=admin_cookies())

    assert response.status_code == 200
    assert "fullsend-router" in response.text
    assert "Repository" in response.text
    assert repository["full_name"] in response.text
    assert "self-hosted" in response.text
    assert "Route Fullsend event" in response.text
    assert "site-router" in response.text
    assert "Site-wide" in response.text
    assert "All repositories" in response.text
    assert f"/ui-legacy/{repository['full_name']}/actions/jobs/{job.id}" in response.text
    assert "must-not-render" not in response.text
    assert "site-token-must-not-render" not in response.text
    assert 'href="/ui-legacy/_admin/runners"' in response.text
