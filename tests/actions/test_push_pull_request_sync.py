"""Which pull requests a push synchronizes, and at which commit.

A push to a branch raised ``pull_request_target`` synchronize activity for
every pull request that branch had ever been the head of, closed and merged
ones included, and stamped each run with the base commit recorded when that
pull request was opened. On a repository whose default branch had once been a
pull request head, every push therefore produced a run pointing at a commit the
branch had long moved past, and checking it out asked the git transport for an
object no ref pointed at.
"""

import pytest
from sqlalchemy import select

from app.models.actions import WorkflowRun
from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.services.workflow_service import process_push_event
from tests.conftest import API, auth_headers


WORKFLOW = """name: sync
on:
  push:
  pull_request_target:
    types: [synchronize]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
"""


async def _repo_with_workflow(client, token, db_session):
    created = await client.post(
        f"{API}/user/repos",
        json={"name": "sync-repo", "auto_init": True},
        headers=auth_headers(token),
    )
    assert created.status_code == 201
    data = created.json()
    put = await client.put(
        f"{API}/repos/{data['full_name']}/contents/.github/workflows/sync.yml",
        json={
            "message": "add workflow",
            "content": __import__("base64").b64encode(WORKFLOW.encode()).decode(),
            "branch": "main",
        },
        headers=auth_headers(token),
    )
    assert put.status_code in (200, 201)
    repository = (
        await db_session.execute(
            select(Repository).where(Repository.full_name == data["full_name"])
        )
    ).scalar_one()
    return repository


async def _pull_request(db_session, repository, user, *, state, merged, base_sha):
    issue = Issue(
        repo_id=repository.id,
        number=900 + (1 if state == "open" else 2) + (10 if merged else 0),
        title=f"{state} pr",
        body="",
        user_id=user.id,
        state=state,
    )
    db_session.add(issue)
    await db_session.flush()
    pr = PullRequest(
        issue_id=issue.id,
        repo_id=repository.id,
        head_ref="main",
        head_sha="b" * 40,
        base_ref="main",
        base_sha=base_sha,
        merged=merged,
    )
    db_session.add(pr)
    await db_session.commit()
    return pr


async def _sync_runs(db_session, repository):
    rows = (
        await db_session.execute(
            select(WorkflowRun).where(
                WorkflowRun.repo_id == repository.id,
                WorkflowRun.event == "pull_request_target",
            )
        )
    ).scalars().all()
    return list(rows)


@pytest.mark.asyncio
async def test_a_closed_pull_request_is_not_synchronized(
    client, db_session, test_user, test_token
):
    repository = await _repo_with_workflow(client, test_token, db_session)
    await _pull_request(
        db_session, repository, test_user,
        state="closed", merged=False, base_sha="a" * 40,
    )
    await process_push_event(
        db_session, repository, test_user, ref_name="main", after_sha=None,
    )
    assert await _sync_runs(db_session, repository) == []


@pytest.mark.asyncio
async def test_a_merged_pull_request_is_not_synchronized(
    client, db_session, test_user, test_token
):
    repository = await _repo_with_workflow(client, test_token, db_session)
    await _pull_request(
        db_session, repository, test_user,
        state="closed", merged=True, base_sha="a" * 40,
    )
    await process_push_event(
        db_session, repository, test_user, ref_name="main", after_sha=None,
    )
    assert await _sync_runs(db_session, repository) == []


@pytest.mark.asyncio
async def test_an_open_pull_request_still_synchronizes(
    client, db_session, test_user, test_token
):
    repository = await _repo_with_workflow(client, test_token, db_session)
    await _pull_request(
        db_session, repository, test_user,
        state="open", merged=False, base_sha="a" * 40,
    )
    await process_push_event(
        db_session, repository, test_user, ref_name="main", after_sha=None,
    )
    assert len(await _sync_runs(db_session, repository)) == 1


@pytest.mark.asyncio
async def test_the_run_uses_the_base_branch_now_not_the_recorded_base(
    client, db_session, test_user, test_token
):
    """pull_request_target exists to run the base branch's current code."""
    repository = await _repo_with_workflow(client, test_token, db_session)
    stale = "a" * 40
    await _pull_request(
        db_session, repository, test_user,
        state="open", merged=False, base_sha=stale,
    )
    await process_push_event(
        db_session, repository, test_user, ref_name="main", after_sha=None,
    )
    runs = await _sync_runs(db_session, repository)
    assert len(runs) == 1
    assert runs[0].head_sha != stale

    # And it is the branch's real tip, which is what a checkout can fetch.
    from app.services.workflow_service import get_ref_sha
    assert runs[0].head_sha == await get_ref_sha(repository.disk_path, "main")
