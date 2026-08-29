"""Tests for the custom Actions runner execution contract."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.actions import Runner
from app.models.actions import Workflow, WorkflowJob, WorkflowRun
from app.models.repository import Repository
from app.services.workflow_service import create_workflow_run
from tests.conftest import API, auth_headers


@pytest.fixture
async def executable_workflow(client, db_session, test_user, test_token):
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "actions-exec-repo"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201

    repo = (await db_session.execute(
        select(Repository).where(Repository.full_name == "testuser/actions-exec-repo")
    )).scalar_one()

    workflow = Workflow(
        repo_id=repo.id,
        name="Executable CI",
        path=".github/workflows/ci.yml",
    )
    db_session.add(workflow)
    await db_session.flush()

    workflow_yaml = {
        "name": "Executable CI",
        "on": ["push"],
        "jobs": {
            "build": {
                "runs-on": ["self-hosted", "linux"],
                "steps": [
                    {
                        "name": "Write output",
                        "if": "true",
                        "run": "printf 'hello from runner\\n'",
                        "shell": "bash",
                        "env": {
                            "STEP_FLAG": "1",
                            "EXPR_FLAG": "${{ steps.route.outputs.stage }}",
                            "TOKEN_FLAG": "${{ github.token }}",
                        },
                    },
                ],
            },
        },
    }
    run = await create_workflow_run(
        db_session,
        workflow,
        workflow_yaml,
        event="push",
        payload={"ref": "refs/heads/main"},
        actor=test_user,
        head_sha="abc123",
        head_branch="main",
    )
    await db_session.commit()
    return repo, workflow, run


@pytest.fixture
async def dependent_workflow(client, db_session, test_user, test_token):
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "actions-dependent-repo"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201

    repo = (await db_session.execute(
        select(Repository).where(Repository.full_name == "testuser/actions-dependent-repo")
    )).scalar_one()

    workflow = Workflow(
        repo_id=repo.id,
        name="Dependent CI",
        path=".github/workflows/dependent.yml",
    )
    db_session.add(workflow)
    await db_session.flush()

    workflow_yaml = {
        "name": "Dependent CI",
        "on": ["push"],
        "jobs": {
            "build": {
                "runs-on": ["self-hosted", "linux"],
                "steps": [{"name": "Fail", "run": "exit 1"}],
            },
            "deploy": {
                "needs": "build",
                "runs-on": ["self-hosted", "linux"],
                "steps": [{"name": "Deploy", "run": "echo deploy"}],
            },
        },
    }
    run = await create_workflow_run(
        db_session,
        workflow,
        workflow_yaml,
        event="push",
        payload={"ref": "refs/heads/main"},
        actor=test_user,
        head_sha="def456",
        head_branch="main",
    )
    await db_session.commit()
    return repo, workflow, run


async def _register_custom_runner(client, repo_full_name: str, token: str) -> str:
    token_resp = await client.post(
        f"{API}/repos/{repo_full_name}/actions/runners/registration-token",
        headers=auth_headers(token),
    )
    assert token_resp.status_code == 200

    register_resp = await client.post(
        f"{API}/actions/runner/register",
        json={
            "token": token_resp.json()["token"],
            "name": "exec-runner",
            "labels": ["self-hosted", "linux"],
            "os": "linux",
        },
    )
    assert register_resp.status_code == 200
    return register_resp.json()["token"]


async def _register_site_wide_runner(client, token: str, name: str = "site-runner") -> dict:
    response = await client.post(
        f"{API}/admin/actions/runners/register",
        headers=auth_headers(token),
        json={
            "name": name,
            "labels": ["self-hosted", "linux", "fullsend-router"],
            "os": "linux",
        },
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_workflow_run_preserves_shell_step_payload(db_session, executable_workflow):
    _repo, _workflow, run = executable_workflow

    job = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run.id)
    )).scalar_one()

    assert job.steps[0]["name"] == "Write output"
    assert job.steps[0]["run"] == "printf 'hello from runner\\n'"
    assert job.steps[0]["shell"] == "bash"
    assert job.steps[0]["env"] == {
        "STEP_FLAG": "1",
        "EXPR_FLAG": "${{ steps.route.outputs.stage }}",
        "TOKEN_FLAG": "${{ github.token }}",
    }


@pytest.mark.asyncio
async def test_custom_runner_poll_complete_and_log_flow(
    client, db_session, executable_workflow, test_token,
):
    _repo, _workflow, run = executable_workflow
    runner_token = await _register_custom_runner(
        client, "testuser/actions-exec-repo", test_token
    )
    runner_headers = {"Authorization": f"Bearer {runner_token}"}

    poll_resp = await client.get(
        f"{API}/repos/testuser/actions-exec-repo/actions/runner/jobs",
        params={"labels": "self-hosted,linux", "timeout": 1},
        headers=runner_headers,
    )
    assert poll_resp.status_code == 200
    job_payload = poll_resp.json()
    assert job_payload["steps"][0]["run"] == "printf 'hello from runner\\n'"
    assert job_payload["steps"][0]["if"] == "true"

    log_resp = await client.post(
        f"{API}/repos/testuser/actions-exec-repo/actions/runner/jobs/{job_payload['job_id']}/logs",
        content=b"hello from runner\n",
        headers=runner_headers,
    )
    assert log_resp.status_code == 200

    complete_resp = await client.post(
        f"{API}/repos/testuser/actions-exec-repo/actions/runner/jobs/{job_payload['job_id']}/complete",
        json={
            "conclusion": "success",
            "steps": [
                {
                    **job_payload["steps"][0],
                    "status": "completed",
                    "conclusion": "success",
                },
            ],
        },
        headers=runner_headers,
    )
    assert complete_resp.status_code == 200

    await db_session.refresh(run)
    assert run.status == "completed"
    assert run.conclusion == "success"

    logs_resp = await client.get(
        f"{API}/repos/testuser/actions-exec-repo/actions/jobs/{job_payload['job_id']}/logs"
    )
    assert logs_resp.status_code == 200
    assert "hello from runner" in logs_resp.text


@pytest.mark.asyncio
async def test_site_wide_runner_requires_admin_and_replaces_named_registration(
    client, db_session, test_token, admin_token,
):
    denied = await client.post(
        f"{API}/admin/actions/runners/register",
        headers=auth_headers(test_token),
        json={"name": "shared-router"},
    )
    assert denied.status_code == 403

    first = await _register_site_wide_runner(client, admin_token, "shared-router")
    second = await _register_site_wide_runner(client, admin_token, "shared-router")

    assert second["runner_id"] == first["runner_id"]
    assert second["token"] != first["token"]
    runners = (
        await db_session.execute(
            select(Runner).where(
                Runner.name == "shared-router",
                Runner.repo_id.is_(None),
                Runner.org_id.is_(None),
            )
        )
    ).scalars().all()
    assert len(runners) == 1


@pytest.mark.asyncio
async def test_site_wide_runner_claims_job_and_repository_runner_cannot_poll_globally(
    client, executable_workflow, test_token, admin_token,
):
    _repo, _workflow, _run = executable_workflow
    site = await _register_site_wide_runner(client, admin_token)
    site_headers = {"Authorization": f"Bearer {site['token']}"}

    poll = await client.get(
        f"{API}/actions/runner/jobs",
        params={"labels": "self-hosted,linux,fullsend-router", "timeout": 1},
        headers=site_headers,
    )
    assert poll.status_code == 200
    assert poll.json()["repository"] == "testuser/actions-exec-repo"

    wrong_scope = await client.get(
        f"{API}/repos/testuser/actions-exec-repo/actions/runner/jobs",
        params={"labels": "self-hosted,linux,fullsend-router", "timeout": 1},
        headers=site_headers,
    )
    assert wrong_scope.status_code == 403

    repository_token = await _register_custom_runner(
        client, "testuser/actions-exec-repo", test_token
    )
    denied = await client.get(
        f"{API}/actions/runner/jobs",
        params={"labels": "self-hosted,linux", "timeout": 1},
        headers={"Authorization": f"Bearer {repository_token}"},
    )
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_job_if_false_is_skipped_and_not_queued(
    executable_workflow, db_session, test_user,
):
    _repo, workflow, _run = executable_workflow
    run = await create_workflow_run(
        db_session,
        workflow,
        {
            "name": "Conditional CI",
            "jobs": {
                "ignored": {
                    "if": "github.event.action != 'labeled' || startsWith(github.event.label.name, 'ready-')",
                    "runs-on": ["self-hosted", "linux"],
                    "steps": [{"name": "Should not run", "run": "exit 1"}],
                },
            },
        },
        event="issues",
        payload={
            "action": "labeled",
            "label": {"name": "duplicate"},
            "ref": "refs/heads/main",
        },
        actor=test_user,
        head_sha="conditional123",
        head_branch="main",
    )
    await db_session.commit()

    job = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run.id)
    )).scalar_one()
    assert job.status == "completed"
    assert job.conclusion == "skipped"
    assert job.steps[0]["conclusion"] == "skipped"
    assert run.status == "completed"
    assert run.conclusion == "success"


@pytest.mark.asyncio
async def test_custom_runner_reclaims_job_from_stale_runner(
    client, db_session, executable_workflow, test_token,
):
    _repo, _workflow, _run = executable_workflow
    first_token = await _register_custom_runner(
        client, "testuser/actions-exec-repo", test_token
    )
    first_headers = {"Authorization": f"Bearer {first_token}"}
    first_poll = await client.get(
        f"{API}/repos/testuser/actions-exec-repo/actions/runner/jobs",
        params={"labels": "self-hosted,linux", "timeout": 1},
        headers=first_headers,
    )
    assert first_poll.status_code == 200
    job_id = first_poll.json()["job_id"]

    first_runner = (await db_session.execute(
        select(Runner).where(Runner.token_hash.is_not(None)).order_by(Runner.id)
    )).scalars().first()
    first_runner.last_heartbeat = datetime.now(timezone.utc) - timedelta(
        seconds=settings.RUNNER_STALE_THRESHOLD_SECONDS + 1
    )
    await db_session.commit()

    second_token = await _register_custom_runner(
        client, "testuser/actions-exec-repo", test_token
    )
    second_poll = await client.get(
        f"{API}/repos/testuser/actions-exec-repo/actions/runner/jobs",
        params={"labels": "self-hosted,linux", "timeout": 1},
        headers={"Authorization": f"Bearer {second_token}"},
    )
    assert second_poll.status_code == 200
    assert second_poll.json()["job_id"] == job_id

    job = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.id == job_id)
    )).scalar_one()
    assert job.status == "in_progress"
    assert job.runner_id != first_runner.id
    assert job.steps[0]["status"] == "queued"


@pytest.mark.asyncio
async def test_actions_runner_registration_broker(
    client, executable_workflow, test_token,
):
    token_resp = await client.post(
        f"{API}/repos/testuser/actions-exec-repo/actions/runners/registration-token",
        headers=auth_headers(test_token),
    )
    assert token_resp.status_code == 200

    register_resp = await client.post(
        f"{API}/actions/runner-registration",
        json={
            "registrationToken": token_resp.json()["token"],
            "agentName": "broker-runner",
            "labels": [{"name": "self-hosted"}, {"name": "linux"}],
            "os": "linux",
        },
    )
    assert register_resp.status_code == 200
    data = register_resp.json()
    assert data["agentId"] == data["runner"]["id"]
    assert data["poolId"] == 1
    assert data["token_schema"] == "OAuthAccessToken"
    assert data["runner"]["name"] == "broker-runner"
    assert data["authorization"]["parameters"]["AccessToken"] == data["token"]


@pytest.mark.asyncio
async def test_custom_runner_failure_skips_dependent_jobs_and_keeps_logs(
    client, db_session, dependent_workflow, test_token,
):
    _repo, _workflow, run = dependent_workflow
    runner_token = await _register_custom_runner(
        client, "testuser/actions-dependent-repo", test_token
    )
    runner_headers = {"Authorization": f"Bearer {runner_token}"}

    poll_resp = await client.get(
        f"{API}/repos/testuser/actions-dependent-repo/actions/runner/jobs",
        params={"labels": "self-hosted,linux", "timeout": 1},
        headers=runner_headers,
    )
    assert poll_resp.status_code == 200
    job_payload = poll_resp.json()
    assert job_payload["name"] == "build"

    log_resp = await client.post(
        f"{API}/repos/testuser/actions-dependent-repo/actions/runner/jobs/{job_payload['job_id']}/logs",
        content=b"build failed\n",
        headers=runner_headers,
    )
    assert log_resp.status_code == 200

    complete_resp = await client.post(
        f"{API}/repos/testuser/actions-dependent-repo/actions/runner/jobs/{job_payload['job_id']}/complete",
        json={
            "conclusion": "failure",
            "steps": [
                {
                    **job_payload["steps"][0],
                    "status": "completed",
                    "conclusion": "failure",
                },
            ],
        },
        headers=runner_headers,
    )
    assert complete_resp.status_code == 200

    jobs = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run.id).order_by(WorkflowJob.name)
    )).scalars().all()
    assert [(job.name, job.status, job.conclusion) for job in jobs] == [
        ("build", "completed", "failure"),
        ("deploy", "completed", "skipped"),
    ]
    await db_session.refresh(run)
    assert run.status == "completed"
    assert run.conclusion == "failure"

    logs_resp = await client.get(
        f"{API}/repos/testuser/actions-dependent-repo/actions/jobs/{job_payload['job_id']}/logs"
    )
    assert logs_resp.status_code == 200
    assert "build failed" in logs_resp.text


@pytest.mark.asyncio
async def test_pool_distributed_task_protocol_flow(
    client, db_session, executable_workflow, test_token,
):
    _repo, _workflow, run = executable_workflow

    token_resp = await client.post(
        f"{API}/repos/testuser/actions-exec-repo/actions/runners/registration-token",
        headers=auth_headers(test_token),
    )
    assert token_resp.status_code == 200

    register_resp = await client.post(
        "/_apis/distributedtask/pools/1/agents",
        json={
            "token": token_resp.json()["token"],
            "agentName": "real-protocol-runner",
            "labels": [{"name": "self-hosted"}, {"name": "linux"}],
            "os": "linux",
        },
    )
    assert register_resp.status_code == 200
    runner_token = register_resp.json()["token"]
    runner_headers = {"Authorization": f"Bearer {runner_token}"}

    agents_resp = await client.get(
        "/_apis/distributedtask/pools/1/agents",
        headers=runner_headers,
    )
    assert agents_resp.status_code == 200
    assert agents_resp.json()["value"][0]["name"] == "real-protocol-runner"

    session_resp = await client.post(
        "/_apis/distributedtask/pools/1/sessions",
        json={"agent": {"id": register_resp.json()["id"]}},
        headers=runner_headers,
    )
    assert session_resp.status_code == 200
    session_id = session_resp.json()["sessionId"]

    message_resp = await client.get(
        f"/_apis/distributedtask/pools/1/sessions/{session_id}/messages",
        params={"lastMessageId": 0},
        headers=runner_headers,
    )
    assert message_resp.status_code == 200
    message = message_resp.json()
    assert message["messageType"] == "PipelineAgentJobRequest"
    body = json.loads(message["body"])
    assert body["steps"][0]["type"] == "Action"
    assert body["steps"][0]["reference"]["type"] == "Script"
    assert body["steps"][0]["inputs"]["map"][0]["Value"] == "printf 'hello from runner\\n'"
    assert body["steps"][0]["environment"]["map"] == [
        {"Key": "STEP_FLAG", "Value": "1"},
        {
            "Key": "EXPR_FLAG",
            "Value": {"type": 3, "expr": "steps.route.outputs.stage"},
        },
        {
            "Key": "TOKEN_FLAG",
            "Value": {"type": 3, "expr": "github.token"},
        },
    ]
    assert body["steps"][0]["condition"] == "true"
    assert body["steps"][0]["contextName"] is None
    assert body["variables"]["system.github.token"]["IsSecret"] is True
    job_token = body["variables"]["system.github.token"]["Value"]
    assert len(job_token.split(".")) == 3
    authenticated = await client.get(
        f"{API}/user",
        headers={"Authorization": f"token {job_token}"},
    )
    assert authenticated.status_code == 200
    assert authenticated.json()["login"] == "testuser"
    assert body["variables"]["system.github.job"]["Value"] == "build"
    github_context = {
        item["k"]: item["v"] for item in body["contextData"]["github"]["d"]
    }
    assert body["contextData"]["github"]["t"] == 2
    assert github_context["repository"] == "testuser/actions-exec-repo"
    assert github_context["ref"] == "refs/heads/main"
    assert github_context["sha"] == "abc123"
    assert github_context["event"] == {
        "t": 2,
        "d": [{"k": "ref", "v": "refs/heads/main"}],
    }
    request_id = body["requestId"]
    timeline_id = body["timeline"]["id"]

    accept_resp = await client.post(
        f"/_apis/distributedtask/pools/1/jobrequests/{request_id}",
        headers=runner_headers,
    )
    assert accept_resp.status_code == 200

    timeline_resp = await client.patch(
        f"/_apis/distributedtask/pools/1/timelines/{timeline_id}/records",
        json={
            "value": [
                {
                    "order": 1,
                    "state": "Completed",
                    "result": "Succeeded",
                }
            ]
        },
        headers=runner_headers,
    )
    assert timeline_resp.status_code == 200


    log_resp = await client.post(
        f"/_apis/distributedtask/pools/1/timelines/{timeline_id}/logs/1",
        content=b"pool protocol log\n",
        headers=runner_headers,
    )
    assert log_resp.status_code == 200

    complete_resp = await client.patch(
        f"/_apis/distributedtask/pools/1/jobrequests/{request_id}",
        json={"result": "Succeeded"},
        headers=runner_headers,
    )
    assert complete_resp.status_code == 200
    assert complete_resp.json()["result"] == "Succeeded"

    await db_session.refresh(run)
    assert run.status == "completed"
    assert run.conclusion == "success"

    logs_resp = await client.get(
        f"{API}/repos/testuser/actions-exec-repo/actions/jobs/{request_id}/logs"
    )
    assert logs_resp.status_code == 200
    assert "pool protocol log" in logs_resp.text

    delete_resp = await client.delete(
        f"/_apis/distributedtask/pools/1/sessions/{session_id}",
        headers=runner_headers,
    )
    assert delete_resp.status_code == 200

    agent_delete_resp = await client.delete(
        f"/_apis/distributedtask/pools/1/agents/{register_resp.json()['id']}",
        headers=runner_headers,
    )
    assert agent_delete_resp.status_code == 200


@pytest.mark.asyncio
async def test_busy_pool_runner_does_not_claim_a_second_job(
    client, db_session, executable_workflow, test_token,
):
    _repo, _workflow, run = executable_workflow
    token_resp = await client.post(
        f"{API}/repos/testuser/actions-exec-repo/actions/runners/registration-token",
        headers=auth_headers(test_token),
    )
    register_resp = await client.post(
        "/_apis/distributedtask/pools/1/agents",
        json={
            "token": token_resp.json()["token"],
            "agentName": "single-job-runner",
            "labels": [{"name": "self-hosted"}, {"name": "linux"}],
            "os": "linux",
        },
    )
    runner_headers = {
        "Authorization": f"Bearer {register_resp.json()['token']}"
    }
    session_resp = await client.post(
        "/_apis/distributedtask/pools/1/sessions",
        json={"agent": {"id": register_resp.json()["id"]}},
        headers=runner_headers,
    )
    messages_url = (
        f"/_apis/distributedtask/pools/1/sessions/"
        f"{session_resp.json()['sessionId']}/messages"
    )

    first_message = await client.get(messages_url, headers=runner_headers)
    assert first_message.status_code == 200

    second_job = WorkflowJob(
        run_id=run.id,
        name="second",
        status="queued",
        labels=["self-hosted", "linux"],
        steps=[{"name": "Second", "run": "true"}],
    )
    db_session.add(second_job)
    await db_session.commit()

    busy_poll = await client.get(messages_url, headers=runner_headers)
    assert busy_poll.status_code == 204
    await db_session.refresh(second_job)
    assert second_job.status == "queued"
    assert second_job.runner_id is None


@pytest.mark.asyncio
async def test_enterprise_runner_registration_and_cross_repo_claim(
    client, db_session, executable_workflow, test_token, admin_token,
):
    denied = await client.post(
        f"{API}/enterprises/breadboard/actions/runners/registration-token",
        headers=auth_headers(test_token),
    )
    assert denied.status_code == 403

    token_resp = await client.post(
        f"{API}/enterprises/breadboard/actions/runners/registration-token",
        headers=auth_headers(admin_token),
    )
    assert token_resp.status_code == 200

    register_resp = await client.post(
        "/_apis/distributedtask/pools/1/agents",
        json={
            "token": token_resp.json()["token"],
            "agentName": "enterprise-runner",
            "labels": [{"name": "self-hosted"}, {"name": "linux"}],
            "os": "linux",
        },
    )
    assert register_resp.status_code == 200
    runner = (await db_session.execute(
        select(Runner).where(Runner.id == register_resp.json()["id"])
    )).scalar_one()
    assert runner.repo_id is None
    assert runner.enterprise_slug == "breadboard"

    runner_headers = {
        "Authorization": f"Bearer {register_resp.json()['token']}"
    }
    session_resp = await client.post(
        "/_apis/distributedtask/pools/1/sessions",
        json={"agent": {"id": runner.id}},
        headers=runner_headers,
    )
    assert session_resp.status_code == 200
    message_resp = await client.get(
        f"/_apis/distributedtask/pools/1/sessions/{session_resp.json()['sessionId']}/messages",
        params={"lastMessageId": 0},
        headers=runner_headers,
    )
    assert message_resp.status_code == 200
    body = json.loads(message_resp.json()["body"])
    github_context = {
        item["k"]: item["v"] for item in body["contextData"]["github"]["d"]
    }
    assert github_context["repository"] == "testuser/actions-exec-repo"

    listed = await client.get(
        f"{API}/enterprises/breadboard/actions/runners",
        headers=auth_headers(admin_token),
    )
    assert listed.status_code == 200
    assert listed.json()["runners"][0]["name"] == "enterprise-runner"
