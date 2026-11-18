"""github.* names who and what started the run, as on GitHub.

Compared side by side with GitHub: actor, actor_id, triggering_actor, api_url,
graphql_url, repository_id, repository_owner_id, run_attempt and workflow_ref
were empty in a step's env on the emulator, and GITHUB_ACTOR,
GITHUB_ACTOR_ID, GITHUB_TRIGGERING_ACTOR and GITHUB_WORKFLOW_REF were unset
on the runner (which exports them from the job message's github context).
"""

import json

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.actions import WorkflowJob
from tests.actions.test_uses_steps import _claim_job, uses_workflow  # noqa: F401

KEYS = ("actor", "actor_id", "triggering_actor", "api_url", "graphql_url",
        "repository_id", "repository_owner_id", "run_attempt", "workflow_ref")


@pytest.mark.asyncio
async def test_step_env_renders_the_identity_values(db_session, test_user, uses_workflow):  # noqa: F811
    repo, workflow, _run = uses_workflow
    from app.services.workflow_service import create_workflow_run

    yaml = {
        "name": "Identity",
        "on": ["push"],
        "jobs": {"show": {"runs-on": ["self-hosted", "linux"], "steps": [
            {"run": "true", "env": {key: "${{ github." + key + " }}" for key in KEYS}},
        ]}},
    }
    second = await create_workflow_run(
        db_session, workflow, yaml, event="push", payload={"ref": "refs/heads/main"},
        actor=test_user, head_sha="abc123", head_branch="main",
    )
    await db_session.commit()
    job = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.run_id == second.id))).scalar_one()
    env = job.steps[0]["env"]
    assert env["actor"] == env["triggering_actor"] == "testuser"
    assert env["actor_id"] == str(test_user.id)
    assert env["api_url"] == f"{settings.BASE_URL}/api/v3"
    assert env["graphql_url"] == f"{settings.BASE_URL}/api/graphql"
    assert env["repository_id"] == str(repo.id)
    assert env["repository_owner_id"] == str(repo.owner_id)
    assert env["run_attempt"] == "1"
    assert env["workflow_ref"] == f"testuser/uses-step-repo/{workflow.path}@refs/heads/main"


@pytest.mark.asyncio
async def test_the_job_message_carries_them_for_the_runner(client, uses_workflow, test_token):  # noqa: F811
    body = await _claim_job(client, test_token)
    github = {item["k"]: item["v"] for item in body["contextData"]["github"]["d"]}
    assert github["actor"] == github["triggering_actor"] == "testuser"
    assert github["actor_id"]
    assert github["workflow_ref"].startswith("testuser/uses-step-repo/.github/workflows/ci.yml@")
    assert json.dumps(body)  # the message still serialises


@pytest.mark.asyncio
async def test_run_and_job_fields_github_fills(client, uses_workflow, test_token):  # noqa: F811
    from tests.conftest import API, auth_headers

    _repo, workflow, run = uses_workflow
    base = f"{API}/repos/testuser/uses-step-repo/actions"
    got = (await client.get(f"{base}/runs/{run.id}", headers=auth_headers(test_token))).json()
    assert got["run_started_at"] == got["created_at"]
    assert got["display_title"] == workflow.name  # a push with no head_commit in its payload
    job = (await client.get(f"{base}/runs/{run.id}/jobs", headers=auth_headers(test_token))).json()["jobs"][0]
    assert job["head_sha"] == "abc123"
    assert job["created_at"]


ATTEMPT_WORKFLOW = """
on: push
jobs:
  only:
    runs-on: ubuntu-latest
    steps:
      - run: echo attempt ${{ github.run_attempt }}
"""


@pytest.mark.asyncio
async def test_a_rerun_renders_its_own_attempt(client, db_session, test_user, test_token):
    from app.services.workflow_service import process_push_event
    from tests.actions.test_consolidation_bug_fixes import _repo_with_workflow
    from tests.conftest import API, auth_headers

    repository = await _repo_with_workflow(
        client, test_token, db_session, name="attempt-repo", workflow=ATTEMPT_WORKFLOW)
    original = (await process_push_event(db_session, repository, test_user, ref_name="main"))[0]
    resp = await client.post(
        f"{API}/repos/{repository.full_name}/actions/runs/{original.id}/rerun", headers=auth_headers(test_token))
    assert resp.status_code == 201, resp.text
    job = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == resp.json()["id"]))).scalar_one()
    assert job.steps[0]["run"] == "echo attempt 2"
    assert job.run_attempt == 2


@pytest.mark.asyncio
async def test_a_push_run_is_titled_by_its_commit_and_knows_branch_protection(
    client, db_session, test_user, test_token,
):
    from app.models.branch import Branch
    from app.services.workflow_service import github_identity_context, process_push_event
    from tests.actions.test_consolidation_bug_fixes import _repo_with_workflow
    from tests.conftest import API, auth_headers

    repository = await _repo_with_workflow(
        client, test_token, db_session, name="title-repo", workflow=ATTEMPT_WORKFLOW)
    run = (await process_push_event(db_session, repository, test_user, ref_name="main"))[0]
    got = (await client.get(
        f"{API}/repos/{repository.full_name}/actions/runs/{run.id}", headers=auth_headers(test_token))).json()
    assert got["display_title"] == "add workflow"  # the head commit's first line

    assert (await github_identity_context(db_session, run))["ref_protected"] is False
    branch = (await db_session.execute(
        select(Branch).where(Branch.repo_id == repository.id, Branch.name == "main"))).scalar_one()
    branch.protected = True
    await db_session.commit()
    assert (await github_identity_context(db_session, run))["ref_protected"] is True
