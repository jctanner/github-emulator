"""Six defects the runner consolidation found, each pinned here.

1. A push processed after its pull request opened fired `synchronize`.
2. A rerun copied job rows without job_key, so dependents waited forever.
3. A runner pod restarting registered a new row each time.
4. A claim whose response was lost stranded the job in progress.
5. A cancelled job kept accepting the runner's writes, so the runner kept going.
6. G45: a job token could write to any repository.
   G46: job_workflow_ref named the caller, not the called workflow.
"""

import base64
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt
from sqlalchemy import select

from app.models.actions import Runner, Workflow, WorkflowJob, WorkflowRun
from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.services.job_token_service import issue_job_token
from app.services.workflow_service import process_push_event
from tests.conftest import API, auth_headers

TWO_JOBS = """
name: two
on: [push]
jobs:
  first:
    runs-on: [self-hosted, linux]
    steps:
      - run: echo one
  second:
    runs-on: [self-hosted, linux]
    needs: [first]
    steps:
      - run: echo two
"""


async def _repo_with_workflow(client, token, db_session, name="fix-repo", workflow=TWO_JOBS):
    created = await client.post(
        f"{API}/user/repos", json={"name": name, "auto_init": True}, headers=auth_headers(token)
    )
    assert created.status_code == 201, created.text
    full_name = created.json()["full_name"]
    put = await client.put(
        f"{API}/repos/{full_name}/contents/.github/workflows/two.yml",
        json={"message": "add workflow", "content": base64.b64encode(workflow.encode()).decode(), "branch": "main"},
        headers=auth_headers(token),
    )
    assert put.status_code in (200, 201), put.text
    repository = (await db_session.execute(
        select(Repository).where(Repository.full_name == full_name)
    )).scalar_one()
    return repository


async def _register(client, full_name, token, name="fix-runner"):
    reg = await client.post(
        f"{API}/repos/{full_name}/actions/runners/registration-token", headers=auth_headers(token)
    )
    assert reg.status_code == 200
    resp = await client.post(
        f"{API}/actions/runner/register",
        json={"token": reg.json()["token"], "name": name, "labels": ["self-hosted", "linux"], "os": "linux"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    data["id"] = data.get("runner_id", data.get("id"))
    return data


async def _claim(client, full_name, runner_token):
    resp = await client.get(
        f"{API}/repos/{full_name}/actions/runner/jobs",
        params={"labels": "self-hosted,linux", "timeout": 1},
        headers={"Authorization": f"Bearer {runner_token}"},
    )
    return resp


# --- 1. synchronize only for a head that moved ------------------------------

@pytest.mark.asyncio
async def test_a_pull_request_already_at_the_pushed_head_is_not_synchronized(
    client, db_session, test_user, test_token
):
    repository = await _repo_with_workflow(
        client, test_token, db_session, name="sync-skip",
        workflow="name: s\non:\n  pull_request_target:\n    types: [synchronize]\njobs:\n  j:\n    runs-on: [self-hosted]\n    steps:\n      - run: true\n",
    )
    issue = Issue(repo_id=repository.id, number=901, title="pr", body="", user_id=test_user.id, state="open")
    db_session.add(issue); await db_session.flush()
    pr = PullRequest(issue_id=issue.id, repo_id=repository.id, head_ref="main",
                     head_sha="c" * 40, base_ref="main", base_sha="d" * 40, merged=False)
    db_session.add(pr); await db_session.commit()

    # The pull request already records the pushed commit: opened after the push.
    await process_push_event(db_session, repository, test_user, ref_name="main", after_sha="c" * 40)
    runs = (await db_session.execute(select(WorkflowRun).where(
        WorkflowRun.repo_id == repository.id, WorkflowRun.event == "pull_request_target"))).scalars().all()
    assert runs == [], "a head that did not move must not synchronize"

    # The head moved: this one synchronizes, and the stored head follows it.
    await process_push_event(db_session, repository, test_user, ref_name="main", after_sha="e" * 40)
    runs = (await db_session.execute(select(WorkflowRun).where(
        WorkflowRun.repo_id == repository.id, WorkflowRun.event == "pull_request_target"))).scalars().all()
    assert len(runs) == 1
    await db_session.refresh(pr)
    assert pr.head_sha == "e" * 40


# --- 2. rerun rebuilds the run ---------------------------------------------

@pytest.mark.asyncio
async def test_rerun_rebuilds_jobs_so_dependents_can_dispatch(
    client, db_session, test_user, test_token
):
    repository = await _repo_with_workflow(client, test_token, db_session, name="rerun-repo")
    runs = await process_push_event(db_session, repository, test_user, ref_name="main")
    assert len(runs) == 1
    original = runs[0]

    resp = await client.post(
        f"{API}/repos/{repository.full_name}/actions/runs/{original.id}/rerun",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201, resp.text
    new_id = resp.json()["id"]
    assert resp.json()["run_attempt"] == 2
    jobs = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == new_id).order_by(WorkflowJob.id)
    )).scalars().all()
    by_key = {j.job_key: j for j in jobs}
    assert set(by_key) == {"first", "second"}, "job keys are what dependents are matched on"
    assert by_key["first"].status == "queued"
    assert by_key["second"].status == "waiting" and by_key["second"].needs == ["first"]

    # Serve and complete the first job; the second must become queued. The
    # original run's first job is still queued too and is older, so drain it.
    runner = await _register(client, repository.full_name, test_token)
    claimed_ids = []
    for _ in range(2):
        claimed = await _claim(client, repository.full_name, runner["token"])
        assert claimed.status_code == 200
        claimed_ids.append(claimed.json()["job_id"])
    assert by_key["first"].id in claimed_ids
    done = await client.post(
        f"{API}/repos/{repository.full_name}/actions/runner/jobs/{by_key['first'].id}/complete",
        json={"conclusion": "success", "steps": [], "step_outputs": {}},
        headers={"Authorization": f"Bearer {runner['token']}"},
    )
    assert done.status_code == 200, done.text
    await db_session.refresh(by_key["second"])
    assert by_key["second"].status == "queued", "the rerun's dependent never left waiting before"


# --- 3. registration reuse ----------------------------------------------------

@pytest.mark.asyncio
async def test_registering_the_same_name_again_reuses_the_row(client, db_session, test_user, test_token):
    repository = await _repo_with_workflow(client, test_token, db_session, name="reg-repo")
    first = await _register(client, repository.full_name, test_token, name="pod-runner")
    second = await _register(client, repository.full_name, test_token, name="pod-runner")
    assert first["id"] == second["id"]
    assert first["token"] != second["token"], "the credential is re-keyed"
    rows = (await db_session.execute(select(Runner).where(Runner.name == "pod-runner"))).scalars().all()
    assert len(rows) == 1
    # The old credential no longer works.
    stale = await _claim(client, repository.full_name, first["token"])
    assert stale.status_code in (401, 403)


# --- 4. a lost claim is requeued -----------------------------------------------

@pytest.mark.asyncio
async def test_a_claim_the_runner_never_acted_on_is_requeued(client, db_session, test_user, test_token):
    repository = await _repo_with_workflow(client, test_token, db_session, name="ack-repo")
    (run,) = await process_push_event(db_session, repository, test_user, ref_name="main")
    runner = await _register(client, repository.full_name, test_token)
    claimed = await _claim(client, repository.full_name, runner["token"])
    assert claimed.status_code == 200
    job_id = claimed.json()["job_id"]

    # Nothing happened for longer than the ack window: no step moved.
    job = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.id == job_id))).scalar_one()
    assert job.status == "in_progress"
    job.started_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    await db_session.commit()

    again = await _claim(client, repository.full_name, runner["token"])
    assert again.status_code == 200 and again.json()["job_id"] == job_id, "the job is handed out again"

    # But a job whose runner has started a step is not a lost claim.
    job = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.id == job_id))).scalar_one()
    job.started_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    job.steps = [{**s, "status": "in_progress"} for s in (job.steps or [])] or [{"name": "x", "status": "in_progress"}]
    await db_session.commit()
    third = await _claim(client, repository.full_name, runner["token"])
    assert third.status_code == 204


# --- 5. a cancelled job refuses the runner's writes ------------------------------

@pytest.mark.asyncio
async def test_a_cancelled_job_tells_the_runner_so(client, db_session, test_user, test_token):
    repository = await _repo_with_workflow(client, test_token, db_session, name="cancel-repo")
    (run,) = await process_push_event(db_session, repository, test_user, ref_name="main")
    runner = await _register(client, repository.full_name, test_token)
    claimed = await _claim(client, repository.full_name, runner["token"])
    job_id = claimed.json()["job_id"]
    cancelled = await client.post(
        f"{API}/repos/{repository.full_name}/actions/runs/{run.id}/cancel", headers=auth_headers(test_token)
    )
    assert cancelled.status_code == 202
    resp = await client.post(
        f"{API}/repos/{repository.full_name}/actions/runner/jobs/{job_id}/logs",
        content=b"still running\n",
        headers={"Authorization": f"Bearer {runner['token']}", "Content-Type": "text/plain"},
    )
    assert resp.status_code == 409
    assert "cancelled" in resp.text


# --- 6. G45 and G46 ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_job_token_cannot_write_to_another_repository(client, db_session, test_user, test_token):
    mine = await _repo_with_workflow(client, test_token, db_session, name="token-home")
    other = await _repo_with_workflow(client, test_token, db_session, name="token-other")
    (run,) = await process_push_event(db_session, mine, test_user, ref_name="main")
    job = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.run_id == run.id))).scalars().first()
    job.permissions = {"issues": "write", "contents": "read", "actions": "write"}
    await db_session.commit()
    headers = {"Authorization": f"Bearer {issue_job_token(job)}"}

    cross = await client.post(f"{API}/repos/{other.full_name}/issues", json={"title": "x"}, headers=headers)
    assert cross.status_code == 403, cross.text
    assert "cannot write to" in cross.text
    own = await client.post(f"{API}/repos/{mine.full_name}/issues", json={"title": "x"}, headers=headers)
    assert own.status_code == 201, own.text
    read = await client.get(f"{API}/repos/{other.full_name}", headers=headers)
    assert read.status_code == 200, "reading another public repository is allowed"


@pytest.mark.asyncio
async def test_job_workflow_ref_names_the_workflow_that_defined_the_job(client, db_session, test_user, test_token):
    repository = await _repo_with_workflow(client, test_token, db_session, name="ref-repo")
    (run,) = await process_push_event(db_session, repository, test_user, ref_name="main")
    job = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.run_id == run.id))).scalars().first()
    job.permissions = {"id-token": "write", "contents": "read"}
    await db_session.commit()

    async def claims():
        resp = await client.get(
            "/actions/oidc/token?api-version=2.0&audience=fullsend-mint",
            headers={"Authorization": f"Bearer {issue_job_token(job)}"},
        )
        assert resp.status_code == 200, resp.text
        return jwt.get_unverified_claims(resp.json()["value"])

    own = await claims()
    assert own["job_workflow_ref"] == own["workflow_ref"], "a job the run's workflow defines"

    job.workflow_ref = "fullsend-ai/fullsend/.github/workflows/reusable-dispatch.yml@refs/heads/main"
    await db_session.commit()
    called = await claims()
    assert called["job_workflow_ref"] == job.workflow_ref
    assert called["workflow_ref"] != called["job_workflow_ref"], "the caller stays in workflow_ref"


def test_called_workflow_ref_shapes():
    from app.services.workflow_service import _called_workflow_ref as f
    assert f("fullsend-ai/fullsend/.github/workflows/reusable-dispatch.yml@main", "fullsend-ai/fullsend", "main") == \
        "fullsend-ai/fullsend/.github/workflows/reusable-dispatch.yml@refs/heads/main"
    assert f("o/r/.github/workflows/x.yml@" + "a" * 40, "o/r", "a" * 40) == "o/r/.github/workflows/x.yml@" + "a" * 40
    assert f("./.github/workflows/local.yml", "me/repo", "refs/heads/dev") == "me/repo/.github/workflows/local.yml@refs/heads/dev"


@pytest.mark.asyncio
async def test_a_runner_re_registering_takes_its_own_job_back(client, db_session, test_user, test_token):
    """A restarted pod is not running the job its previous self held."""
    repository = await _repo_with_workflow(client, test_token, db_session, name="restart-repo")
    await process_push_event(db_session, repository, test_user, ref_name="main")
    first = await _register(client, repository.full_name, test_token, name="pod")
    claimed = await _claim(client, repository.full_name, first["token"])
    assert claimed.status_code == 200
    job_id = claimed.json()["job_id"]

    second = await _register(client, repository.full_name, test_token, name="pod")
    assert second["id"] == first["id"]
    again = await _claim(client, repository.full_name, second["token"])
    assert again.status_code == 200 and again.json()["job_id"] == job_id
