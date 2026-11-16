"""A job's GITHUB_TOKEN on the /installation routes, as on GitHub.

GitHub's GITHUB_TOKEN is an installation token for the run's repository:
GET /installation/repositories lists that repository. Clients probe the
route to tell an installation token from a personal access token.
"""

import pytest
from sqlalchemy import select

from app.models.actions import Workflow, WorkflowJob, WorkflowRun
from app.models.repository import Repository
from app.services.job_token_service import issue_job_token
from tests.conftest import auth_headers

API = "/api/v3"


@pytest.fixture
async def job_token(client, db_session, test_user, test_token):
    resp = await client.post(f"{API}/user/repos", json={"name": "jobtok"}, headers=auth_headers(test_token))
    assert resp.status_code == 201, resp.text
    repo = (await db_session.execute(select(Repository).where(Repository.name == "jobtok"))).scalar_one()
    workflow = Workflow(repo_id=repo.id, name="W", path=".github/workflows/w.yml")
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id, repo_id=repo.id, head_sha="abc", head_branch="main",
        event="workflow_dispatch", run_number=1, actor_id=test_user.id,
    )
    db_session.add(run)
    await db_session.flush()
    job = WorkflowJob(run_id=run.id, name="j", permissions={"contents": "read"})
    db_session.add(job)
    await db_session.commit()
    return issue_job_token(job)


@pytest.mark.asyncio
async def test_a_job_token_lists_its_own_repository(client, job_token):
    resp = await client.get(f"{API}/installation/repositories", headers={"Authorization": f"Bearer {job_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["repository_selection"] == "selected"
    assert [r["full_name"] for r in body["repositories"]] == ["testuser/jobtok"]
    assert body["total_count"] == 1


@pytest.mark.asyncio
async def test_a_personal_access_token_is_still_refused(client, test_token):
    resp = await client.get(f"{API}/installation/repositories", headers=auth_headers(test_token))
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_a_refused_credential_is_bad_credentials(client):
    refused = await client.get(f"{API}/user", headers={"Authorization": "Bearer ghp_not_a_token"})
    assert refused.status_code == 401
    assert refused.json()["message"] == "Bad credentials"
    missing = await client.get(f"{API}/user")
    assert missing.status_code == 401
    assert missing.json()["message"] == "Requires authentication"
