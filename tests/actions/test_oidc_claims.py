"""Coverage for the Actions OIDC token endpoint.

The endpoint used to accept a static bearer string and let the caller name its
own ``subject`` and ``audience``. Any process holding that string could then
mint a token describing any repository, which made downstream claim validation
meaningless. These tests pin the replacement: the caller authenticates with its
own job's token, chooses only the audience, and every identifying claim is
derived from the run.
"""

import pytest
from jose import jwt
from sqlalchemy import select

from app.models.actions import Workflow, WorkflowJob, WorkflowRun
from app.models.repository import Repository
from app.services.job_token_service import issue_job_token
from tests.conftest import auth_headers  # noqa: F401 - keeps fixture import style


TRIAGE_PERMISSIONS = {
    "actions": "write",
    "contents": "read",
    "id-token": "write",
    "issues": "write",
}


async def _seed_job(db_session, repo_full_name, permissions, event="issues"):
    """Create a workflow, run, and job for the given repository."""
    repository = (
        await db_session.execute(
            select(Repository).where(Repository.full_name == repo_full_name)
        )
    ).scalar_one()

    workflow = Workflow(
        repo_id=repository.id,
        name="Fullsend",
        path=".github/workflows/fullsend.yaml",
    )
    db_session.add(workflow)
    await db_session.flush()

    run = WorkflowRun(
        workflow_id=workflow.id,
        repo_id=repository.id,
        head_sha="a" * 40,
        head_branch="main",
        event=event,
        run_number=7,
        run_attempt=1,
        actor_id=repository.owner_id,
        trigger_payload={},
    )
    db_session.add(run)
    await db_session.flush()

    job = WorkflowJob(
        run_id=run.id,
        name="Triage",
        job_key="triage",
        workflow_name="Fullsend",
        permissions=permissions,
        steps=[],
    )
    db_session.add(job)
    await db_session.commit()
    return repository, run, job


@pytest.mark.asyncio
async def test_issuer_metadata_and_keys(client):
    configuration = await client.get("/.well-known/openid-configuration")
    assert configuration.status_code == 200
    assert configuration.json()["issuer"] == "http://testserver"
    keys = await client.get("/.well-known/jwks.json")
    assert keys.status_code == 200
    assert keys.json()["keys"][0]["alg"] == "RS256"


@pytest.mark.asyncio
async def test_claims_describe_the_run_not_the_request(
    client, db_session, test_repo_with_init
):
    _owner, _name, repo = test_repo_with_init
    repository, run, job = await _seed_job(
        db_session, repo["full_name"], TRIAGE_PERMISSIONS
    )

    response = await client.get(
        "/actions/oidc/token?api-version=2.0&audience=fullsend-mint",
        headers={"Authorization": f"Bearer {issue_job_token(job)}"},
    )
    assert response.status_code == 200
    claims = jwt.get_unverified_claims(response.json()["value"])

    assert claims["iss"] == "http://testserver"
    assert claims["aud"] == "fullsend-mint"
    assert claims["repository"] == repo["full_name"]
    assert claims["repository_owner"] == repo["full_name"].split("/")[0]
    assert claims["repository_id"] == str(repository.id)
    assert claims["run_id"] == str(run.id)
    assert claims["run_number"] == "7"
    assert claims["sha"] == "a" * 40
    assert claims["event_name"] == "issues"
    assert claims["workflow"] == "Fullsend"
    assert claims["workflow_ref"] == (
        f"{repo['full_name']}/.github/workflows/fullsend.yaml@refs/heads/main"
    )
    # The subject is derived, so a caller cannot choose it.
    assert claims["sub"] == f"repo:{repo['full_name']}:ref:refs/heads/main"


@pytest.mark.asyncio
async def test_caller_cannot_choose_its_own_subject(
    client, db_session, test_repo_with_init
):
    """The vulnerability this endpoint used to have, pinned shut."""
    _owner, _name, repo = test_repo_with_init
    _repository, _run, job = await _seed_job(
        db_session, repo["full_name"], TRIAGE_PERMISSIONS
    )

    response = await client.get(
        "/actions/oidc/token?api-version=2.0&audience=fullsend-mint"
        "&subject=repo:someone-else/private:ref:refs/heads/main",
        headers={"Authorization": f"Bearer {issue_job_token(job)}"},
    )
    assert response.status_code == 200
    claims = jwt.get_unverified_claims(response.json()["value"])
    assert claims["sub"] == f"repo:{repo['full_name']}:ref:refs/heads/main"
    assert claims["repository"] == repo["full_name"]


@pytest.mark.asyncio
async def test_pull_request_events_get_their_own_subject_form(
    client, db_session, test_repo_with_init
):
    _owner, _name, repo = test_repo_with_init
    _repository, _run, job = await _seed_job(
        db_session, repo["full_name"], TRIAGE_PERMISSIONS, event="pull_request"
    )

    response = await client.get(
        "/actions/oidc/token?api-version=2.0&audience=fullsend-mint",
        headers={"Authorization": f"Bearer {issue_job_token(job)}"},
    )
    assert response.status_code == 200
    claims = jwt.get_unverified_claims(response.json()["value"])
    assert claims["sub"] == f"repo:{repo['full_name']}:pull_request"


@pytest.mark.asyncio
async def test_static_bearer_string_is_refused(client):
    response = await client.get(
        "/actions/oidc/token?api-version=2.0&audience=fullsend-mint",
        headers={"Authorization": "Bearer fullsend-action-request"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_missing_authorization_is_refused(client):
    response = await client.get("/actions/oidc/token?api-version=2.0")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_job_without_id_token_write_is_refused(
    client, db_session, test_repo_with_init
):
    """Mirrors GitHub: the job must ask for id-token: write."""
    _owner, _name, repo = test_repo_with_init
    _repository, _run, job = await _seed_job(
        db_session,
        repo["full_name"],
        {"contents": "read", "issues": "read", "pull-requests": "read"},
    )

    response = await client.get(
        "/actions/oidc/token?api-version=2.0&audience=fullsend-mint",
        headers={"Authorization": f"Bearer {issue_job_token(job)}"},
    )
    assert response.status_code == 403
    body = response.json()
    assert "id-token: write" in (body.get("message") or body.get("detail") or "")


@pytest.mark.asyncio
async def test_audience_defaults_to_the_repository_owner(
    client, db_session, test_repo_with_init
):
    _owner, _name, repo = test_repo_with_init
    _repository, _run, job = await _seed_job(
        db_session, repo["full_name"], TRIAGE_PERMISSIONS
    )

    response = await client.get(
        "/actions/oidc/token?api-version=2.0",
        headers={"Authorization": f"Bearer {issue_job_token(job)}"},
    )
    assert response.status_code == 200
    claims = jwt.get_unverified_claims(response.json()["value"])
    assert claims["aud"] == f"http://testserver/{repo['full_name'].split('/')[0]}"
