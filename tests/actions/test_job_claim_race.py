"""Two runners polling at once must not both claim the same queued job.

GitHub hands each job to exactly one runner. The emulator's claim paths read
the queued jobs and then wrote the assignment unconditionally, so two polls
that read the same job before either wrote it both ran it. These tests force
that interleaving: both polls finish their read before either claims.
"""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Select

from app.api import actions_dispatch, actions_distributed_task
from app.models.actions import Runner, Workflow, WorkflowJob
from app.models.repository import Repository
from app.services.workflow_service import create_workflow_run
from tests.conftest import API, auth_headers


@pytest.fixture
async def queued_job(client, db_session, test_user, test_token):
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "claim-race-repo"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    repo = (await db_session.execute(
        select(Repository).where(Repository.full_name == "testuser/claim-race-repo")
    )).scalar_one()

    workflow = Workflow(repo_id=repo.id, name="CI", path=".github/workflows/ci.yml")
    db_session.add(workflow)
    await db_session.flush()
    run = await create_workflow_run(
        db_session,
        workflow,
        {
            "name": "CI",
            "on": ["push"],
            "jobs": {
                "build": {
                    "runs-on": ["self-hosted", "linux"],
                    "steps": [{"run": "true"}],
                },
            },
        },
        event="push",
        payload={"ref": "refs/heads/main"},
        actor=test_user,
        head_sha="abc123",
        head_branch="main",
    )
    for name in ("runner-a", "runner-b"):
        db_session.add(Runner(
            name=name, status="online", labels=["self-hosted", "linux"],
        ))
    await db_session.commit()

    jobs = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run.id)
    )).scalars().all()
    assert [job.status for job in jobs] == ["queued"]
    return jobs[0]


def _is_candidate_select(statement) -> bool:
    if not isinstance(statement, Select):
        return False
    sql = str(statement)
    return "FROM workflow_jobs JOIN workflow_runs" in sql and "workflow_jobs.status =" in sql


def _gate_after_candidate_select(session, after_select):
    """Run *after_select* once, right after the session reads queued jobs."""
    original = session.execute
    state = {"gated": False}

    async def execute(statement, *args, **kwargs):
        result = await original(statement, *args, **kwargs)
        if not state["gated"] and _is_candidate_select(statement):
            state["gated"] = True
            # Buffer the rows so this read holds nothing while it waits.
            result = result.freeze()()
            await after_select()
        return result

    session.execute = execute


async def _poll_dispatch(runner, db):
    return await actions_dispatch._poll_for_jobs(
        runner, db, labels="self-hosted,linux", timeout=1, repository_id=None,
    )


async def _poll_distributed(runner, db):
    return await actions_distributed_task._claim_next_job(
        runner, db, "http://testserver",
    )


@pytest.mark.parametrize("claim", [_poll_dispatch, _poll_distributed],
                         ids=["custom-runner-poll", "distributed-task-poll"])
async def test_two_runners_polling_at_once_claim_a_job_once(
    db_engine, db_session, queued_job, claim,
):
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    b_selected = asyncio.Event()
    a_claimed = asyncio.Event()

    async with factory() as session_a, factory() as session_b:
        runner_a = (await session_a.execute(
            select(Runner).where(Runner.name == "runner-a")
        )).scalar_one()
        runner_b = (await session_b.execute(
            select(Runner).where(Runner.name == "runner-b")
        )).scalar_one()

        # A reads, then waits until B has read the same queued job too.
        _gate_after_candidate_select(session_a, b_selected.wait)

        async def b_after_select():
            b_selected.set()
            await a_claimed.wait()

        # B reads, then waits until A has finished claiming.
        _gate_after_candidate_select(session_b, b_after_select)

        async def poll_a():
            try:
                return await claim(runner_a, session_a)
            finally:
                a_claimed.set()

        result_a, result_b = await asyncio.wait_for(
            asyncio.gather(poll_a(), claim(runner_b, session_b)), timeout=15,
        )

    assert isinstance(result_a, dict), "the first poll should receive the job"
    assert getattr(result_b, "status_code", None) == 204, (
        "the second poll claimed a job another runner already holds"
    )

    job_id = queued_job.id
    db_session.expire_all()
    job = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.id == job_id)
    )).scalar_one()
    assert job.status == "in_progress"
    assert job.runner_name == "runner-a"
    runners = {
        runner.name: runner
        for runner in (await db_session.execute(select(Runner))).scalars()
    }
    assert runners["runner-a"].busy is True
    assert runners["runner-b"].busy is False
    assert runners["runner-b"].status == "online"



async def _register_pool_runner(client, test_token, name):
    token_resp = await client.post(
        f"{API}/repos/testuser/claim-race-repo/actions/runners/registration-token",
        headers=auth_headers(test_token),
    )
    register_resp = await client.post(
        "/_apis/distributedtask/pools/1/agents",
        json={
            "token": token_resp.json()["token"],
            "agentName": name,
            "labels": [{"name": "self-hosted"}, {"name": "linux"}],
            "os": "linux",
        },
    )
    headers = {"Authorization": f"Bearer {register_resp.json()['token']}"}
    session_resp = await client.post(
        "/_apis/distributedtask/pools/1/sessions",
        json={"agent": {"id": register_resp.json()["id"]}},
        headers=headers,
    )
    messages_url = (
        f"/_apis/distributedtask/pools/1/sessions/"
        f"{session_resp.json()['sessionId']}/messages"
    )
    return headers, messages_url


async def test_renewing_a_job_request_another_runner_holds_is_job_not_found(
    client, test_token, queued_job,
):
    """The runner gives up a job only on TaskAgentJobNotFoundException.

    A bare 404 sends the upstream runner into its lock-renewal retry loop,
    so a runner that does not own the job never returns to polling.
    """
    owner_headers, owner_messages = await _register_pool_runner(
        client, test_token, "pool-owner",
    )
    other_headers, _ = await _register_pool_runner(client, test_token, "pool-other")

    claimed = await client.get(owner_messages, headers=owner_headers)
    assert claimed.status_code == 200

    renewal_url = (
        f"/_apis/distributedtask/pools/1/jobrequests/{queued_job.id}"
        "?lockToken=00000000-0000-0000-0000-000000000000"
    )
    renewal_body = {"requestId": queued_job.id}

    renewed = await client.patch(renewal_url, json=renewal_body, headers=owner_headers)
    assert renewed.status_code == 200

    stolen = await client.patch(renewal_url, json=renewal_body, headers=other_headers)
    assert stolen.status_code == 404
    error = stolen.json()
    assert error["typeKey"] == "TaskAgentJobNotFoundException"
    # The plain 404's field stays for clients that read it.
    assert error["detail"] == "Job request not found"
    assert error["typeName"].startswith(
        "GitHub.DistributedTask.WebApi.TaskAgentJobNotFoundException,"
    )
