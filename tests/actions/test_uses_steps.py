"""`uses:` steps on the upstream-runner path.

The real actions/runner receives a `uses:` step as a repository reference,
asks the service for download info before the job starts, and fetches each
action archive with the job token as a Basic credential. The emulator once
rendered such a step as an empty script that reported success: checkout and
setup-node went green, did nothing, and `node --version` failed two steps
later. These tests pin the whole path: the payload shape, the download-info
call, the archive proxy, and the one refusal left (container actions).
"""

import base64
import json

import pytest
from sqlalchemy import select

from app.api import actions_distributed_task as dt
from app.models.actions import Workflow
from app.models.repository import Repository
from app.services.action_download_service import UpstreamActionError, parse_uses
from app.services.workflow_service import create_workflow_run
from tests.conftest import API, auth_headers

SHA = "1d96c772d19495a3b5c517cd2bc0cb401ea0529f"


# --- the grammar, mirrored from PipelineTemplateConverter ---------------------

@pytest.mark.parametrize(
    "uses, expected",
    [
        ("actions/checkout@v4",
         {"type": "Repository", "repositoryType": "GitHub",
          "name": "actions/checkout", "ref": "v4"}),
        ("actions/setup-node@" + SHA,
         {"type": "Repository", "repositoryType": "GitHub",
          "name": "actions/setup-node", "ref": SHA}),
        ("owner/repo/sub/dir@main",
         {"type": "Repository", "repositoryType": "GitHub",
          "name": "owner/repo", "ref": "main", "path": "sub/dir"}),
        ("./.github/actions/local",
         {"type": "Repository", "repositoryType": "self",
          "path": "./.github/actions/local"}),
    ],
)
def test_parse_uses_matches_the_runner_grammar(uses, expected):
    assert parse_uses(uses).as_step_reference() == expected


@pytest.mark.parametrize("uses", ["docker://alpine:3", "actions/checkout", "checkout@v4", "a/b@"])
def test_values_the_runner_would_reject_are_rejected_here(uses):
    assert parse_uses(uses) is None


# --- the job payload ----------------------------------------------------------

@pytest.fixture
async def uses_workflow(client, db_session, test_user, test_token):
    resp = await client.post(
        f"{API}/user/repos", json={"name": "uses-step-repo"}, headers=auth_headers(test_token)
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
                    {"name": "Node", "id": "node", "uses": "actions/setup-node@v4",
                     "with": {"node-version": "20", "cache": "${{ steps.route.outputs.cache }}"}},
                    {"name": "Version", "run": "node --version"},
                    {"name": "Local", "uses": "./.github/actions/local"},
                    {"name": "Container", "uses": "docker://alpine:3"},
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


async def _claim_job(client, test_token):
    """Register a runner, open a session, and take the job message."""
    token_resp = await client.post(
        f"{API}/repos/testuser/uses-step-repo/actions/runners/registration-token",
        headers=auth_headers(test_token),
    )
    assert token_resp.status_code == 200
    register_resp = await client.post(
        "/_apis/distributedtask/pools/1/agents",
        json={"token": token_resp.json()["token"], "agentName": "uses-runner",
              "labels": [{"name": "self-hosted"}, {"name": "linux"}], "os": "linux"},
    )
    assert register_resp.status_code == 200
    headers = {"Authorization": f"Bearer {register_resp.json()['token']}"}
    session_resp = await client.post(
        "/_apis/distributedtask/pools/1/sessions",
        json={"agent": {"id": register_resp.json()["id"]}}, headers=headers,
    )
    assert session_resp.status_code == 200
    message_resp = await client.get(
        f"/_apis/distributedtask/pools/1/sessions/{session_resp.json()['sessionId']}/messages",
        params={"lastMessageId": 0}, headers=headers,
    )
    assert message_resp.status_code == 200
    message = message_resp.json()
    assert message["messageType"] == "PipelineAgentJobRequest"
    return json.loads(message["body"])


def _inputs(step):
    return {e["Key"]: e["Value"] for e in step["inputs"]["map"]}


@pytest.mark.asyncio
async def test_uses_steps_become_repository_references(client, uses_workflow, test_token):
    body = await _claim_job(client, test_token)
    steps = body["steps"]
    assert len(steps) == 5

    assert steps[0]["reference"] == {
        "type": "Repository", "repositoryType": "GitHub",
        "name": "actions/checkout", "ref": "v4",
    }
    assert _inputs(steps[0]) == {}

    # `with:` travels as the step inputs, with a runner-time expression kept
    # as an expression, and the step's id is its context name so later steps
    # can read its outputs.
    assert steps[1]["reference"]["name"] == "actions/setup-node"
    assert _inputs(steps[1]) == {
        "node-version": "20",
        "cache": {"type": 3, "expr": "steps.route.outputs.cache"},
    }
    assert steps[1]["contextName"] == "node"
    assert steps[1]["displayName"] == "Node"

    # A plain run: step is untouched.
    assert steps[2]["reference"] == {"type": "Script"}
    assert _inputs(steps[2])["script"] == "node --version"

    # A local action is taken from the checked-out workspace.
    assert steps[3]["reference"] == {
        "type": "Repository", "repositoryType": "self", "path": "./.github/actions/local",
    }


@pytest.mark.asyncio
async def test_a_container_action_still_fails_loudly(client, uses_workflow, test_token):
    """The one thing this path still cannot run says so and exits non-zero."""
    body = await _claim_job(client, test_token)
    step = body["steps"][4]
    assert step["reference"] == {"type": "Script"}
    script = _inputs(step)["script"]
    assert "::error::uses: docker://alpine:3 was not run" in script
    # One quoted word: the ';' in the reason must not end the echo.
    assert script.startswith("echo '::error::") and script.count("\n") == 1
    assert "NOT executed" in script
    assert script.rstrip().endswith("exit 1")


# --- download info and the archive proxy --------------------------------------

@pytest.mark.asyncio
async def test_download_info_resolves_and_points_back_here(
    client, uses_workflow, test_token, monkeypatch
):
    body = await _claim_job(client, test_token)
    job_token = body["variables"]["system.github.token"]["Value"]
    plan_id = body["plan"]["planId"]

    asked = []

    async def fake_resolve(name, ref):
        asked.append((name, ref))
        return SHA
    monkeypatch.setattr(dt, "resolve_action_sha", fake_resolve)

    resp = await client.post(
        f"/_apis/distributedtask/hubs/Build/plans/{plan_id}/actionsdownloadinfo",
        params={"api-version": "6.0-preview.1", "jobId": body["jobId"]},
        json={"actions": [
            {"nameWithOwner": "actions/checkout", "ref": "v4", "path": ""},
            {"nameWithOwner": "actions/setup-node", "ref": "v4", "path": ""},
        ]},
        headers={"Authorization": f"Bearer {job_token}"},
    )
    assert resp.status_code == 200, resp.text
    actions = resp.json()["actions"]
    # Keyed the way ActionManager looks them up: name@ref.
    assert set(actions) == {"actions/checkout@v4", "actions/setup-node@v4"}
    info = actions["actions/checkout@v4"]
    assert info["resolvedSha"] == SHA
    assert info["resolvedNameWithOwner"] == "actions/checkout"
    # The archive comes from this service, not github.com: the runner will
    # present the emulator's job token, which GitHub would reject.
    assert info["tarballUrl"].endswith(
        f"/_apis/distributedtask/actions/archives/actions/checkout/{SHA}/archive.tar.gz"
    )
    assert info["zipballUrl"].endswith(f"/{SHA}/archive.zip")
    assert "authentication" not in info
    assert sorted(asked) == [("actions/checkout", "v4"), ("actions/setup-node", "v4")]


@pytest.mark.asyncio
async def test_download_info_needs_the_job_token(client, uses_workflow, test_token):
    body = await _claim_job(client, test_token)
    resp = await client.post(
        f"/_apis/distributedtask/hubs/Build/plans/{body['plan']['planId']}/actionsdownloadinfo",
        json={"actions": []},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_an_unknown_action_is_unresolvable_not_retried(
    client, uses_workflow, test_token, monkeypatch
):
    """A 404 carrying the runner's own typeKey, so it fails once, not thrice."""
    body = await _claim_job(client, test_token)
    job_token = body["variables"]["system.github.token"]["Value"]

    async def fake_resolve(name, ref):
        raise UpstreamActionError(f"{name}@{ref} does not exist upstream", not_found=True)
    monkeypatch.setattr(dt, "resolve_action_sha", fake_resolve)

    resp = await client.post(
        f"/_apis/distributedtask/hubs/Build/plans/{body['plan']['planId']}/actionsdownloadinfo",
        json={"actions": [{"nameWithOwner": "nobody/nothing", "ref": "v9"}]},
        headers={"Authorization": f"Bearer {job_token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["typeKey"] == "UnresolvableActionDownloadInfoException"
    assert "nobody/nothing@v9" in resp.json()["message"]


@pytest.mark.asyncio
async def test_archive_is_served_to_the_job_with_basic_auth(
    client, uses_workflow, test_token, monkeypatch, tmp_path
):
    body = await _claim_job(client, test_token)
    job_token = body["variables"]["system.github.token"]["Value"]
    archive = tmp_path / f"{SHA}.tar.gz"
    archive.write_bytes(b"\x1f\x8b not really a tarball")

    async def fake_fetch(name, sha, fmt):
        assert (name, sha, fmt) == ("actions/checkout", SHA, "tar.gz")
        return str(archive)
    monkeypatch.setattr(dt, "fetch_action_archive", fake_fetch)

    # Exactly what ActionManager.CreateAuthHeader sends.
    basic = base64.b64encode(f"x-access-token:{job_token}".encode()).decode()
    resp = await client.get(
        f"/_apis/distributedtask/actions/archives/actions/checkout/{SHA}/archive.tar.gz",
        headers={"Authorization": f"Basic {basic}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.content == archive.read_bytes()
    assert resp.headers["content-type"] == "application/gzip"


@pytest.mark.asyncio
async def test_archive_refuses_a_foreign_credential(client, uses_workflow, test_token):
    basic = base64.b64encode(b"x-access-token:not-a-job-token").decode()
    resp = await client.get(
        f"/_apis/distributedtask/actions/archives/actions/checkout/{SHA}/archive.tar.gz",
        headers={"Authorization": f"Basic {basic}"},
    )
    assert resp.status_code == 401
