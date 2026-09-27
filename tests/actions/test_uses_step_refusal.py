"""A `uses:` step on the upstream-runner path fails where it stands.

The distributed-task protocol hands every step to the real actions/runner as
a Script reference, and the emulator serves no action downloads, so an action
cannot be run on that path. Before this, a `uses:` step was rendered as an
empty script: `actions/checkout@v4` and `actions/setup-node@v4` both reported
success, checked nothing out, and installed nothing, and the failure surfaced
only two steps later as `node: command not found`.

This pins the interim behaviour: the step is still a script, but one that
annotates the run with the action it did not execute and exits non-zero.
"""

import json

import pytest
from sqlalchemy import select

from app.models.actions import Workflow
from app.models.repository import Repository
from app.services.workflow_service import create_workflow_run
from tests.conftest import API, auth_headers


@pytest.fixture
async def uses_workflow(client, db_session, test_user, test_token):
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "uses-step-repo"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    repo = (await db_session.execute(
        select(Repository).where(Repository.full_name == "testuser/uses-step-repo")
    )).scalar_one()
    workflow = Workflow(repo_id=repo.id, name="Uses CI", path=".github/workflows/ci.yml")
    db_session.add(workflow)
    await db_session.flush()

    workflow_yaml = {
        "name": "Uses CI",
        "on": ["push"],
        "jobs": {
            "build": {
                "runs-on": ["self-hosted", "linux"],
                "steps": [
                    {"uses": "actions/checkout@v4"},
                    {"name": "Node", "uses": "actions/setup-node@v4",
                     "with": {"node-version": "20"}},
                    {"name": "Version", "run": "node --version"},
                ],
            },
        },
    }
    run = await create_workflow_run(
        db_session, workflow, workflow_yaml, event="push",
        payload={"ref": "refs/heads/main"}, actor=test_user,
        head_sha="abc123", head_branch="main",
    )
    await db_session.commit()
    return repo, workflow, run


async def _claim_job_body(client, test_token):
    token_resp = await client.post(
        f"{API}/repos/testuser/uses-step-repo/actions/runners/registration-token",
        headers=auth_headers(test_token),
    )
    assert token_resp.status_code == 200
    register_resp = await client.post(
        "/_apis/distributedtask/pools/1/agents",
        json={
            "token": token_resp.json()["token"],
            "agentName": "uses-probe-runner",
            "labels": [{"name": "self-hosted"}, {"name": "linux"}],
            "os": "linux",
        },
    )
    assert register_resp.status_code == 200
    headers = {"Authorization": f"Bearer {register_resp.json()['token']}"}
    session_resp = await client.post(
        "/_apis/distributedtask/pools/1/sessions",
        json={"agent": {"id": register_resp.json()["id"]}},
        headers=headers,
    )
    assert session_resp.status_code == 200
    message_resp = await client.get(
        f"/_apis/distributedtask/pools/1/sessions/{session_resp.json()['sessionId']}/messages",
        params={"lastMessageId": 0},
        headers=headers,
    )
    assert message_resp.status_code == 200
    message = message_resp.json()
    assert message["messageType"] == "PipelineAgentJobRequest"
    return json.loads(message["body"])


def _script(step):
    values = {e["Key"]: e["Value"] for e in step["inputs"]["map"]}
    return values["script"], values.get("shell")


@pytest.mark.asyncio
async def test_uses_step_is_refused_loudly(client, db_session, uses_workflow, test_token):
    body = await _claim_job_body(client, test_token)
    steps = body["steps"]
    assert len(steps) == 3

    for step, action in zip(steps[:2], ["actions/checkout@v4", "actions/setup-node@v4"]):
        assert step["reference"]["type"] == "Script"
        script, shell = _script(step)
        # Not an empty script that succeeds: it names the action, annotates
        # the run, says the action was not executed, and fails.
        assert script != "", "a uses: step rendered as an empty, passing script"
        assert f"::error::uses: '{action}'" in script
        assert "NOT executed" in script
        assert script.rstrip().endswith("exit 1")
        assert shell == "bash"

    # The plain run: step is untouched.
    script, shell = _script(steps[2])
    assert script == "node --version"
    assert shell is None


@pytest.mark.asyncio
async def test_uses_step_keeps_its_display_name(client, db_session, uses_workflow, test_token):
    """The failure shows under the step's own name, so it reads as that step."""
    body = await _claim_job_body(client, test_token)
    assert body["steps"][1]["displayName"] == "Node"
