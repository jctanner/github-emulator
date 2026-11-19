"""gh in an upstream-runner step talks to the emulator, with the workflow's token.

gh ignores GITHUB_API_URL, and for any host but github.com it reads
GH_ENTERPRISE_TOKEN, not GH_TOKEN. Without GH_HOST every gh call in a step
went to api.github.com and failed with "Bad credentials".
"""

import pytest
from sqlalchemy import select

from app.api.actions_distributed_task import _gh_host
from app.models.actions import Workflow
from app.models.repository import Repository
from app.services.workflow_service import create_workflow_run
from tests.actions.test_uses_steps import _claim_job
from tests.conftest import API, auth_headers


@pytest.mark.parametrize("url, host", [
    ("https://ghemu.localhost:443", "ghemu.localhost"),
    ("https://ghemu.localhost", "ghemu.localhost"),
    ("http://127.0.0.1:80", "127.0.0.1"),
    ("http://127.0.0.1:8000", "127.0.0.1:8000"),
])
def test_gh_host_drops_only_the_default_port(url, host):
    assert _gh_host(url) == host


@pytest.fixture
async def gh_workflow(client, db_session, test_user, test_token):
    resp = await client.post(
        f"{API}/user/repos", json={"name": "uses-step-repo"}, headers=auth_headers(test_token)
    )
    assert resp.status_code == 201
    repo = (await db_session.execute(
        select(Repository).where(Repository.full_name == "testuser/uses-step-repo")
    )).scalar_one()
    workflow = Workflow(repo_id=repo.id, name="gh", path=".github/workflows/gh.yml")
    db_session.add(workflow)
    await db_session.flush()
    await create_workflow_run(
        db_session, workflow, {
            "name": "gh", "on": ["push"],
            "jobs": {"j": {
                "runs-on": ["self-hosted", "linux"],
                "steps": [
                    {"name": "minted", "run": "gh api user",
                     "env": {"GH_TOKEN": "${{ steps.app.outputs.token }}"}},
                    {"name": "none", "run": "gh --version"},
                    {"name": "own-host", "run": "gh api user",
                     "env": {"GH_HOST": "elsewhere.example", "GH_TOKEN": "t",
                             "GH_ENTERPRISE_TOKEN": "explicit"}},
                ],
            }},
        }, event="push", payload={"ref": "refs/heads/main"}, actor=test_user,
        head_sha="abc123", head_branch="main",
    )
    await db_session.commit()


def _environment(step: dict) -> dict:
    return {item["Key"]: item["Value"] for item in step["environment"]["map"]}


@pytest.mark.asyncio
async def test_steps_point_gh_at_the_emulator_with_the_workflows_token(
    client, gh_workflow, test_token
):
    body = await _claim_job(client, test_token)
    minted, none, own = (_environment(step) for step in body["steps"])
    expected_host = _gh_host(body["contextData"]["github"]["d"][
        [entry["k"] for entry in body["contextData"]["github"]["d"]].index("server_url")
    ]["v"])

    # A token the step chose is mirrored, as the same expression.
    assert minted["GH_HOST"] == expected_host
    assert minted["GH_ENTERPRISE_TOKEN"] == {"type": 3, "expr": "steps.app.outputs.token"}
    assert minted["GITHUB_ENTERPRISE_TOKEN"] == minted["GH_ENTERPRISE_TOKEN"]

    # No token is invented for a step that named none.
    assert none["GH_HOST"] == expected_host
    assert "GH_ENTERPRISE_TOKEN" not in none

    # What the workflow sets itself wins.
    assert own["GH_HOST"] == "elsewhere.example"
    assert own["GH_ENTERPRISE_TOKEN"] == "explicit"
