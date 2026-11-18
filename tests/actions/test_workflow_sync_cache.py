"""Workflow lookups re-read the default branch only when it changed.

Every request that finds a workflow by id or file name (get a workflow, list
its runs, dispatch it) and the workflow list synchronized the workflow table
from the default branch first: ``git ls-tree``, one ``git show`` per file,
a YAML parse each, and a write. A client polling
``/actions/workflows/{file}/runs`` paid that on every call. The table is now
re-synchronized only when the default branch's ``.github/workflows`` tree,
or the default branch itself, changed since the last sync.
"""

import asyncio
import base64

import pytest
from sqlalchemy import select

from app.git.bare_repo import set_default_branch
from app.models.repository import Repository
from app.services import workflow_service
from tests.conftest import API, auth_headers

pytestmark = pytest.mark.asyncio

OWNER = "testuser"


def _workflow(name: str) -> str:
    return f"name: {name}\non: [workflow_dispatch]\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo {name}\n"


async def _repo(client, token, name, actions_enabled=True):
    created = await client.post(
        f"{API}/user/repos", json={"name": name, "auto_init": True}, headers=auth_headers(token)
    )
    assert created.status_code == 201, created.text
    if not actions_enabled:
        # No push dispatches, so no push syncs the workflow table: only the
        # lookup itself can notice a changed workflow file.
        response = await client.put(
            f"{API}/repos/{OWNER}/{name}/actions/permissions",
            json={"enabled": False}, headers=auth_headers(token),
        )
        assert response.status_code == 204, response.text
    return f"{API}/repos/{OWNER}/{name}"


async def _put_file(client, token, base, path, content, branch="main", sha=None):
    body = {"message": f"write {path}", "content": base64.b64encode(content.encode()).decode(), "branch": branch}
    if sha:
        body["sha"] = sha
    response = await client.put(f"{base}/contents/{path}", json=body, headers=auth_headers(token))
    assert response.status_code in (200, 201), response.text
    return response.json()["content"]["sha"]


async def _delete_file(client, token, base, path, sha):
    response = await client.request(
        "DELETE", f"{base}/contents/{path}",
        json={"message": f"delete {path}", "sha": sha, "branch": "main"},
        headers=auth_headers(token),
    )
    assert response.status_code == 200, response.text


def _count_detections(monkeypatch):
    calls = []
    detect = workflow_service.detect_workflows

    async def counting(repo_disk_path, ref="HEAD"):
        calls.append(ref)
        return await detect(repo_disk_path, ref)

    monkeypatch.setattr(workflow_service, "detect_workflows", counting)
    return calls


async def test_repeated_lookups_detect_workflows_once(client, test_token, monkeypatch):
    base = await _repo(client, test_token, "poll-repo")
    await _put_file(client, test_token, base, ".github/workflows/ci.yml", _workflow("ci"))
    calls = _count_detections(monkeypatch)
    headers = auth_headers(test_token)

    for _ in range(2):
        response = await client.get(f"{base}/actions/workflows/ci.yml/runs?per_page=10", headers=headers)
        assert response.status_code == 200, response.text
    assert len(calls) == 1

    assert (await client.get(f"{base}/actions/workflows/ci.yml", headers=headers)).status_code == 200
    assert (await client.get(f"{base}/actions/workflows", headers=headers)).status_code == 200
    assert len(calls) == 1


async def test_a_push_is_visible_on_the_next_lookup(client, test_token):
    base = await _repo(client, test_token, "push-repo", actions_enabled=False)
    headers = auth_headers(test_token)
    sha = await _put_file(client, test_token, base, ".github/workflows/ci.yml", _workflow("first"))

    response = await client.get(f"{base}/actions/workflows/ci.yml", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "first"
    assert (await client.get(f"{base}/actions/workflows/other.yml", headers=headers)).status_code == 404

    # A changed workflow.
    sha = await _put_file(client, test_token, base, ".github/workflows/ci.yml", _workflow("second"), sha=sha)
    response = await client.get(f"{base}/actions/workflows/ci.yml/runs", headers=headers)
    assert response.status_code == 200, response.text
    response = await client.get(f"{base}/actions/workflows/ci.yml", headers=headers)
    assert response.json()["name"] == "second"

    # An added workflow.
    await _put_file(client, test_token, base, ".github/workflows/other.yml", _workflow("other"))
    response = await client.get(f"{base}/actions/workflows/other.yml", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "active"

    # A removed workflow keeps its row, disabled, and stays disabled.
    await _delete_file(client, test_token, base, ".github/workflows/ci.yml", sha)
    for _ in range(2):
        response = await client.get(f"{base}/actions/workflows/ci.yml", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "disabled_manually"
    listed = (await client.get(f"{base}/actions/workflows", headers=headers)).json()
    assert {w["path"]: w["state"] for w in listed["workflows"]} == {
        ".github/workflows/ci.yml": "disabled_manually",
        ".github/workflows/other.yml": "active",
    }


async def test_a_default_branch_change_is_visible_on_the_next_lookup(client, test_token, db_session):
    base = await _repo(client, test_token, "branch-repo", actions_enabled=False)
    headers = auth_headers(test_token)
    main = (await client.get(f"{base}/git/ref/heads/main", headers=headers)).json()["object"]["sha"]
    created = await client.post(
        f"{base}/git/refs", json={"ref": "refs/heads/dev", "sha": main}, headers=headers
    )
    assert created.status_code == 201, created.text
    await _put_file(client, test_token, base, ".github/workflows/dev.yml", _workflow("dev"), branch="dev")
    assert (await client.get(f"{base}/actions/workflows/dev.yml", headers=headers)).status_code == 404

    repository = (await db_session.execute(
        select(Repository).where(Repository.full_name == f"{OWNER}/branch-repo")
    )).scalar_one()
    assert await set_default_branch(repository.disk_path, "dev")
    patched = await client.patch(base, json={"default_branch": "dev"}, headers=headers)
    assert patched.status_code == 200, patched.text

    response = await client.get(f"{base}/actions/workflows/dev.yml", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "active"


async def test_a_branch_push_resyncs_the_next_lookup(client, test_token):
    """A push to another branch syncs that branch's workflows into the table;
    the next lookup reconciles them against the default branch again, as it
    did before the cache."""
    base = await _repo(client, test_token, "feature-repo")
    headers = auth_headers(test_token)
    await _put_file(client, test_token, base, ".github/workflows/ci.yml", _workflow("ci"))
    assert (await client.get(f"{base}/actions/workflows/ci.yml", headers=headers)).status_code == 200

    main = (await client.get(f"{base}/git/ref/heads/main", headers=headers)).json()["object"]["sha"]
    created = await client.post(
        f"{base}/git/refs", json={"ref": "refs/heads/feature", "sha": main}, headers=headers
    )
    assert created.status_code == 201, created.text
    await _put_file(
        client, test_token, base, ".github/workflows/feature.yml", _workflow("feature"), branch="feature"
    )

    response = await client.get(f"{base}/actions/workflows/feature.yml", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "disabled_manually"


async def test_overlapping_lookups_sync_once_and_keep_the_cache(client, test_token, monkeypatch):
    base = await _repo(client, test_token, "overlap-repo", actions_enabled=False)
    await _put_file(client, test_token, base, ".github/workflows/ci.yml", _workflow("ci"))
    workflow_service._synced_workflows.clear()
    calls = _count_detections(monkeypatch)
    headers = auth_headers(test_token)

    responses = await asyncio.gather(*[
        client.get(f"{base}/actions/workflows/ci.yml/runs", headers=headers) for _ in range(4)
    ])
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert len(calls) == 1
    assert (await client.get(f"{base}/actions/workflows", headers=headers)).status_code == 200
    assert len(calls) == 1


async def test_a_sync_that_ends_after_a_lookup_clears_the_cache(client, test_token, db_session, monkeypatch):
    base = await _repo(client, test_token, "late-sync-repo", actions_enabled=False)
    await _put_file(client, test_token, base, ".github/workflows/ci.yml", _workflow("ci"))
    workflow_service._synced_workflows.clear()
    repository = (await db_session.execute(select(Repository).where(Repository.name == "late-sync-repo"))).scalar_one()

    # A sync of another ref starts first and pauses while it reads the files.
    started, release = asyncio.Event(), asyncio.Event()
    detect = workflow_service.detect_workflows

    async def paused(repo_disk_path, ref="HEAD"):
        if ref == "refs/heads/other":
            started.set()
            await release.wait()
        return await detect(repo_disk_path, ref)

    monkeypatch.setattr(workflow_service, "detect_workflows", paused)
    late = asyncio.create_task(workflow_service.sync_workflows_to_db(db_session, repository, "refs/heads/other"))
    await started.wait()

    # A lookup syncs the default branch and caches it meanwhile.
    response = await client.get(f"{base}/actions/workflows", headers=auth_headers(test_token))
    assert response.status_code == 200, response.text
    assert repository.id in workflow_service._synced_workflows

    # The earlier sync finishing after it drops the entry again.
    release.set()
    await late
    assert repository.id not in workflow_service._synced_workflows
