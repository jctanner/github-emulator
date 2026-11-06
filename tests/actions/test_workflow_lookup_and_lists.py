"""Find a workflow by file name, list its runs, and list repository artifacts.

A CI driver waiting on a workflow finds it by file name, lists that
workflow's runs, and reads recent artifacts across the repository. Fullsend's
behavior-test drivers do exactly this (github-emulator#17). The emulator
accepted a file name only on the dispatch route, had no per-workflow runs
list, had no repository-wide artifacts list, and reported ``total_count`` as
the length of the returned page rather than the number of matching runs.
"""

import pytest

from app.models.actions import Workflow, WorkflowRun
from tests.conftest import API, auth_headers

pytestmark = pytest.mark.asyncio

BASE = f"{API}/repos/testuser/init-repo/actions"


async def _fixture(db_session, test_user, repo):
    """Two workflows; ``ci.yml`` has three runs and ``other.yml`` one."""
    ci = Workflow(repo_id=repo["id"], name="ci", path=".github/workflows/ci.yml")
    other = Workflow(repo_id=repo["id"], name="other", path=".github/workflows/other.yml")
    db_session.add_all([ci, other])
    await db_session.flush()
    runs = []
    for number, (workflow, event, branch, status, conclusion) in enumerate([
        (ci, "push", "main", "completed", "success"),
        (other, "push", "main", "completed", "success"),
        (ci, "workflow_dispatch", "main", "completed", "failure"),
        (ci, "push", "feature", "queued", None),
    ], start=1):
        run = WorkflowRun(
            workflow_id=workflow.id, repo_id=repo["id"], head_sha=f"sha{number}",
            head_branch=branch, event=event, status=status, conclusion=conclusion,
            run_number=number, actor_id=test_user.id,
        )
        db_session.add(run)
        await db_session.flush()
        runs.append(run)
    await db_session.commit()
    return ci, other, runs


async def test_get_workflow_by_file_name(client, db_session, test_user, test_token, test_repo_with_init):
    _owner, _repo, repo = test_repo_with_init
    ci, _other, _runs = await _fixture(db_session, test_user, repo)
    headers = auth_headers(test_token)

    for workflow_id in (str(ci.id), "ci.yml"):
        response = await client.get(f"{BASE}/workflows/{workflow_id}", headers=headers)
        assert response.status_code == 200, (workflow_id, response.text)
        assert response.json()["id"] == ci.id
        assert response.json()["path"] == ".github/workflows/ci.yml"
    assert (await client.get(f"{BASE}/workflows/missing.yml", headers=headers)).status_code == 404
    assert (await client.get(f"{BASE}/workflows/999999", headers=headers)).status_code == 404


async def test_list_runs_for_a_workflow(client, db_session, test_user, test_token, test_repo_with_init):
    _owner, _repo, repo = test_repo_with_init
    ci, _other, runs = await _fixture(db_session, test_user, repo)
    headers = auth_headers(test_token)
    ci_runs = [runs[3].id, runs[2].id, runs[0].id]  # newest first

    for workflow_id in (str(ci.id), "ci.yml"):
        response = await client.get(f"{BASE}/workflows/{workflow_id}/runs", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total_count"] == 3
        assert [item["id"] for item in body["workflow_runs"]] == ci_runs

    paged = await client.get(f"{BASE}/workflows/ci.yml/runs?per_page=1&page=2", headers=headers)
    assert paged.json()["total_count"] == 3
    assert [item["id"] for item in paged.json()["workflow_runs"]] == [ci_runs[1]]
    assert 'rel="next"' in paged.headers["Link"] and 'rel="prev"' in paged.headers["Link"]

    async def ids(query):
        response = await client.get(f"{BASE}/workflows/ci.yml/runs?{query}", headers=headers)
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["workflow_runs"]]

    assert await ids("event=workflow_dispatch") == [runs[2].id]
    assert await ids("branch=feature") == [runs[3].id]
    assert await ids("head_sha=sha1") == [runs[0].id]
    # ``status`` accepts a status or a conclusion, as on GitHub.
    assert await ids("status=queued") == [runs[3].id]
    assert await ids("status=failure") == [runs[2].id]
    assert (await client.get(f"{BASE}/workflows/missing.yml/runs", headers=headers)).status_code == 404


async def test_repository_runs_total_count_is_the_full_count(client, db_session, test_user, test_token, test_repo_with_init):
    _owner, _repo, repo = test_repo_with_init
    await _fixture(db_session, test_user, repo)

    response = await client.get(f"{BASE}/runs?per_page=2", headers=auth_headers(test_token))
    assert response.status_code == 200
    assert response.json()["total_count"] == 4
    assert len(response.json()["workflow_runs"]) == 2


async def test_list_repository_artifacts(client, db_session, test_user, test_token, test_repo_with_init):
    _owner, _repo, repo = test_repo_with_init
    _ci, _other, runs = await _fixture(db_session, test_user, repo)
    headers = auth_headers(test_token)
    uploaded = []
    for run, name in ((runs[0], "evidence"), (runs[1], "logs"), (runs[2], "evidence")):
        response = await client.post(
            f"{BASE}/runs/{run.id}/artifacts", headers=headers,
            json={"name": name, "files": {"result.json": "{}"}},
        )
        assert response.status_code == 201, response.text
        uploaded.append(response.json()["id"])

    response = await client.get(f"{BASE}/artifacts?per_page=100", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_count"] == 3
    assert [item["id"] for item in body["artifacts"]] == list(reversed(uploaded))
    first = body["artifacts"][-1]
    assert first["workflow_run"]["id"] == runs[0].id
    assert first["expired"] is False

    named = (await client.get(f"{BASE}/artifacts?name=evidence", headers=headers)).json()
    assert named["total_count"] == 2
    assert {item["workflow_run"]["id"] for item in named["artifacts"]} == {runs[0].id, runs[2].id}

    paged = await client.get(f"{BASE}/artifacts?per_page=2&page=2", headers=headers)
    assert paged.json()["total_count"] == 3
    assert [item["id"] for item in paged.json()["artifacts"]] == [uploaded[0]]


async def test_repository_artifacts_on_a_public_repository_need_no_credentials(client, db_session, test_user, test_token, test_repo_with_init):
    _owner, _repo, repo = test_repo_with_init
    _ci, _other, runs = await _fixture(db_session, test_user, repo)
    uploaded = await client.post(
        f"{BASE}/runs/{runs[0].id}/artifacts", headers=auth_headers(test_token),
        json={"name": "public", "files": {"result.json": "{}"}},
    )
    assert uploaded.status_code == 201, uploaded.text

    response = await client.get(f"{BASE}/artifacts")
    assert response.status_code == 200, response.text
    assert response.json()["total_count"] == 1


async def test_repository_artifacts_on_a_private_repository_stay_hidden(client, test_token):
    created = await client.post(
        f"{API}/user/repos", json={"name": "private-artifacts", "private": True}, headers=auth_headers(test_token)
    )
    assert created.status_code == 201, created.text
    url = f"{API}/repos/testuser/private-artifacts/actions/artifacts"
    assert (await client.get(url)).status_code == 404
    assert (await client.get(url, headers=auth_headers(test_token))).status_code == 200


async def test_private_repository_collaborator_can_read_actions(client, db_session, test_user, test_token, test_repo_with_init):
    """A collaborator sees the private repository, so its Actions reads must not 404."""
    import hashlib
    import secrets

    from app.models.token import PersonalAccessToken
    from app.models.user import User

    created = await client.post(
        f"{API}/user/repos", json={"name": "private-actions", "private": True}, headers=auth_headers(test_token)
    )
    assert created.status_code == 201, created.text
    reader = User(login="reader", email="reader@example.com", hashed_password="x", site_admin=False)
    db_session.add(reader)
    await db_session.commit()
    await db_session.refresh(reader)
    raw = f"ghp_{secrets.token_hex(20)}"
    db_session.add(PersonalAccessToken(
        user_id=reader.id, name="reader", token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        token_prefix=raw[:8], scopes=["repo"],
    ))
    await db_session.commit()
    base = f"{API}/repos/testuser/private-actions/actions"
    for path in ("/artifacts", "/runs", "/workflows"):
        assert (await client.get(base + path, headers=auth_headers(raw))).status_code == 404, path

    added = await client.put(
        f"{API}/repos/testuser/private-actions/collaborators/reader",
        json={"permission": "pull"}, headers=auth_headers(test_token),
    )
    assert added.status_code in (201, 204), added.text
    for path in ("/artifacts", "/runs", "/workflows"):
        response = await client.get(base + path, headers=auth_headers(raw))
        assert response.status_code == 200, (path, response.text)
