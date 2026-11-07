"""GraphQL updateRefs: compare-and-swap ref updates (github-emulator#21).

Fullsend's code and fix agents publish signed commits by building them with
the REST Git Data API (blobs, trees, commits), then moving the branch with

    mutation($r: ID!, $u: [RefUpdate!]!) {
      updateRefs(input: {repositoryId: $r, refUpdates: $u}) { clientMutationId }
    }

where each update is ``{name, beforeOid, afterOid, force}`` and ``beforeOid``
is all zeros for a new branch. The batch applies all or none, each update
only while its ref still has ``beforeOid``.
"""

import base64
import time

import pytest
from jose import jwt
from sqlalchemy import select

from app.models.actions import WorkflowRun
from app.models.branch import Branch
from tests.conftest import API, auth_headers
from tests.test_branch_protection_readiness import _protection_body

pytestmark = pytest.mark.asyncio

ZERO = "0" * 40
MUTATION = (
    "mutation($r: ID!, $u: [RefUpdate!]!) "
    "{ updateRefs(input: {repositoryId: $r, refUpdates: $u, clientMutationId: \"m1\"}) { clientMutationId } }"
)
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


async def _repo(client, token, name="refs-repo"):
    created = await client.post(f"{API}/user/repos", json={"name": name, "auto_init": True}, headers=auth_headers(token))
    assert created.status_code == 201, created.text
    repo_id = base64.b64encode(f"Repository:{created.json()['id']}".encode()).decode()
    return repo_id, await _head(client, token, name, "main")


async def _head(client, token, name, branch):
    response = await client.get(f"{API}/repos/testuser/{name}/git/ref/heads/{branch}", headers=auth_headers(token))
    return response.json()["object"]["sha"] if response.status_code == 200 else None


async def _commit(client, token, name, parent, path, text):
    """A commit on ``parent`` made through the Git Data API, as Fullsend's
    signed push makes it; no ref moves."""
    headers = auth_headers(token)
    base_tree = (await client.get(f"{API}/repos/testuser/{name}/git/commits/{parent}", headers=headers)).json()["tree"]["sha"]
    tree = await client.post(
        f"{API}/repos/testuser/{name}/git/trees", headers=headers,
        json={"base_tree": base_tree, "tree": [{"path": path, "mode": "100644", "type": "blob", "content": text}]},
    )
    assert tree.status_code == 201, tree.text
    commit = await client.post(
        f"{API}/repos/testuser/{name}/git/commits", headers=headers,
        json={"message": f"add {path}", "tree": tree.json()["sha"], "parents": [parent]},
    )
    assert commit.status_code == 201, commit.text
    return commit.json()["sha"]


async def _update(client, token, repo_id, *updates):
    response = await client.post(
        "/api/graphql", headers=auth_headers(token),
        json={"query": MUTATION, "variables": {"r": repo_id, "u": [
            {"name": n, "beforeOid": b, "afterOid": a, "force": f} for n, b, a, f in updates
        ]}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _error(response):
    assert response.get("errors"), response
    return response["errors"][0].get("type"), response["errors"][0]["message"]


async def test_creates_and_fast_forwards_a_branch(client, test_user, test_token):
    repo_id, main = await _repo(client, test_token)
    first = await _commit(client, test_token, "refs-repo", main, "a.txt", "a\n")

    created = await _update(client, test_token, repo_id, ("refs/heads/feature", ZERO, first, False))
    assert created == {"data": {"updateRefs": {"clientMutationId": "m1"}}}
    assert await _head(client, test_token, "refs-repo", "feature") == first

    second = await _commit(client, test_token, "refs-repo", first, "b.txt", "b\n")
    moved = await _update(client, test_token, repo_id, ("refs/heads/feature", first, second, False))
    assert "errors" not in moved, moved
    assert await _head(client, test_token, "refs-repo", "feature") == second
    branches = (await client.get(f"{API}/repos/testuser/refs-repo/branches/feature")).json()
    assert branches["commit"]["sha"] == second


async def test_a_stale_before_oid_changes_nothing(client, test_user, test_token):
    repo_id, main = await _repo(client, test_token)
    first = await _commit(client, test_token, "refs-repo", main, "a.txt", "a\n")

    stale = await _update(client, test_token, repo_id, ("refs/heads/main", first, first, False))
    assert _error(stale)[0] == "STALE_DATA"
    exists = await _update(client, test_token, repo_id, ("refs/heads/main", ZERO, first, False))
    assert _error(exists)[0] == "STALE_DATA"
    missing = await _update(client, test_token, repo_id, ("refs/heads/nope", main, first, False))
    assert _error(missing)[0] == "STALE_DATA"
    assert await _head(client, test_token, "refs-repo", "main") == main
    assert await _head(client, test_token, "refs-repo", "nope") is None


async def test_non_fast_forward_needs_force(client, test_user, test_token):
    repo_id, main = await _repo(client, test_token)
    first = await _commit(client, test_token, "refs-repo", main, "a.txt", "a\n")
    sibling = await _commit(client, test_token, "refs-repo", main, "b.txt", "b\n")
    assert "errors" not in await _update(client, test_token, repo_id, ("refs/heads/feature", ZERO, first, False))

    refused = await _update(client, test_token, repo_id, ("refs/heads/feature", first, sibling, False))
    assert _error(refused) == ("UNPROCESSABLE", "Update to refs/heads/feature is not a fast forward")
    assert await _head(client, test_token, "refs-repo", "feature") == first

    forced = await _update(client, test_token, repo_id, ("refs/heads/feature", first, sibling, True))
    assert "errors" not in forced, forced
    assert await _head(client, test_token, "refs-repo", "feature") == sibling


async def test_a_batch_applies_all_or_nothing(client, test_user, test_token):
    repo_id, main = await _repo(client, test_token)
    first = await _commit(client, test_token, "refs-repo", main, "a.txt", "a\n")

    failed = await _update(
        client, test_token, repo_id,
        ("refs/heads/one", ZERO, first, False),
        ("refs/heads/main", first, first, False),  # stale
    )
    assert _error(failed)[0] == "STALE_DATA"
    assert await _head(client, test_token, "refs-repo", "one") is None

    both = await _update(
        client, test_token, repo_id,
        ("refs/heads/one", ZERO, first, False),
        ("refs/tags/v1", ZERO, first, False),
    )
    assert "errors" not in both, both
    assert await _head(client, test_token, "refs-repo", "one") == first
    tag = await client.get(f"{API}/repos/testuser/refs-repo/git/ref/tags/v1", headers=auth_headers(test_token))
    assert tag.json()["object"]["sha"] == first


async def test_deletes_a_branch(client, test_user, test_token, db_session):
    repo_id, main = await _repo(client, test_token)
    assert "errors" not in await _update(client, test_token, repo_id, ("refs/heads/gone", ZERO, main, False))

    deleted = await _update(client, test_token, repo_id, ("refs/heads/gone", main, ZERO, False))
    assert "errors" not in deleted, deleted
    assert await _head(client, test_token, "refs-repo", "gone") is None
    rows = (await db_session.execute(select(Branch).where(Branch.name == "gone"))).scalars().all()
    assert rows == []

    again = await _update(client, test_token, repo_id, ("refs/heads/gone", None, ZERO, False))
    assert _error(again)[0] == "UNPROCESSABLE"
    default = await _update(client, test_token, repo_id, ("refs/heads/main", main, ZERO, False))
    assert _error(default) == ("UNPROCESSABLE", "Cannot delete the default branch refs/heads/main")


async def test_rejects_bad_input(client, test_user, test_token):
    repo_id, main = await _repo(client, test_token)
    for update, text in (
        (("heads/feature", ZERO, main, False), "not a fully qualified ref name"),
        (("refs/heads/bad..name", ZERO, main, False), "not a valid ref name"),
        (("refs/heads/x", ZERO, "nothex", False), "not a valid object id"),
        (("refs/heads/x", ZERO, "1" * 40, False), "does not exist"),
    ):
        kind, message = _error(await _update(client, test_token, repo_id, update))
        assert kind == "UNPROCESSABLE" and text in message, (update, message)
    twice = await _update(client, test_token, repo_id, ("refs/heads/x", ZERO, main, False), ("refs/heads/x", ZERO, main, False))
    assert "more than once" in _error(twice)[1]


async def test_branch_protection(client, test_user, test_token):
    repo_id, main = await _repo(client, test_token)
    first = await _commit(client, test_token, "refs-repo", main, "a.txt", "a\n")
    sibling = await _commit(client, test_token, "refs-repo", main, "b.txt", "b\n")
    assert "errors" not in await _update(client, test_token, repo_id, ("refs/heads/release", ZERO, first, False))
    protected = await client.put(
        f"{API}/repos/testuser/refs-repo/branches/release/protection",
        json=_protection_body(), headers=auth_headers(test_token),
    )
    assert protected.status_code == 200, protected.text

    forced = await _update(client, test_token, repo_id, ("refs/heads/release", first, sibling, True))
    assert "Cannot force-update the protected branch" in _error(forced)[1]
    deleted = await _update(client, test_token, repo_id, ("refs/heads/release", first, ZERO, False))
    assert "Cannot delete the protected branch" in _error(deleted)[1]
    assert await _head(client, test_token, "refs-repo", "release") == first


async def test_moving_a_pull_request_head_synchronizes_it(client, test_user, test_token, db_session):
    repo_id, main = await _repo(client, test_token)
    headers = auth_headers(test_token)
    put = await client.put(
        f"{API}/repos/testuser/refs-repo/contents/.github/workflows/sync.yml", headers=headers,
        json={"message": "add workflow", "content": base64.b64encode(WORKFLOW.encode()).decode(), "branch": "main"},
    )
    assert put.status_code in (200, 201), put.text
    main = await _head(client, test_token, "refs-repo", "main")
    first = await _commit(client, test_token, "refs-repo", main, "a.txt", "a\n")
    assert "errors" not in await _update(client, test_token, repo_id, ("refs/heads/feature", ZERO, first, False))
    pull = await client.post(
        f"{API}/repos/testuser/refs-repo/pulls", headers=headers,
        json={"title": "feature", "head": "feature", "base": "main"},
    )
    assert pull.status_code == 201, pull.text

    second = await _commit(client, test_token, "refs-repo", first, "b.txt", "b\n")
    assert "errors" not in await _update(client, test_token, repo_id, ("refs/heads/feature", first, second, False))

    refreshed = (await client.get(f"{API}/repos/testuser/refs-repo/pulls/{pull.json()['number']}", headers=headers)).json()
    assert refreshed["head"]["sha"] == second
    runs = (await db_session.execute(select(WorkflowRun).where(WorkflowRun.event == "pull_request_target"))).scalars().all()
    assert len(runs) == 1
    pushes = (await db_session.execute(
        select(WorkflowRun).where(WorkflowRun.event == "push", WorkflowRun.head_branch == "feature")
    )).scalars().all()
    assert {run.head_sha for run in pushes} == {first, second}


async def test_who_may_update(client, test_user, test_token, admin_token, db_session):
    repo_id, main = await _repo(client, test_token)
    private = await client.post(f"{API}/user/repos", json={"name": "secret", "private": True, "auto_init": True},
                                headers=auth_headers(test_token))
    private_id = base64.b64encode(f"Repository:{private.json()['id']}".encode()).decode()
    from tests.test_graphql_repository_visibility import _user_token

    _outsider, outsider = await _user_token(db_session, "outsider")
    assert _error(await _update(client, outsider, repo_id, ("refs/heads/x", ZERO, main, False)))[0] == "FORBIDDEN"
    assert _error(await _update(client, outsider, private_id, ("refs/heads/x", ZERO, main, False)))[0] == "NOT_FOUND"

    # An installation token needs contents: write.
    for app_id, contents in (("6001", "read"), ("6002", "write")):
        created = await client.post(
            f"{API}/admin/apps", headers=auth_headers(admin_token),
            json={"app_id": app_id, "name": f"r-{app_id}", "slug": f"r-{app_id}", "permissions": {"contents": contents}},
        )
        installation = await client.post(
            f"{API}/admin/apps/{app_id}/installations", headers=auth_headers(admin_token),
            json={"account_login": "testuser", "account_type": "User", "repositories": ["testuser/refs-repo"],
                  "permissions": {"contents": contents}},
        )
        now = int(time.time())
        app_jwt = jwt.encode({"iss": app_id, "iat": now - 10, "exp": now + 60}, created.json()["private_key"], algorithm="RS256")
        token = (await client.post(
            f"{API}/app/installations/{installation.json()['id']}/access_tokens",
            headers={"Authorization": f"Bearer {app_jwt}"}, json={},
        )).json()["token"]
        response = await _update(client, token, repo_id, (f"refs/heads/app-{app_id}", ZERO, main, False))
        if contents == "read":
            assert _error(response)[0] == "FORBIDDEN"
        else:
            assert "errors" not in response, response


async def test_a_git_push_to_a_pull_request_head_synchronizes_it(client, test_user, test_token, db_session, db_engine, monkeypatch):
    """The git transport's post-push work recorded the pull request's new
    head before raising push activity, which then took the pull request as
    already synchronized: no synchronize run followed a git push."""
    import os
    import subprocess

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    import app.database as database
    from app.git import smart_http
    from app.models.repository import Repository

    monkeypatch.setattr(database, "async_session", async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False))
    repo_id, main = await _repo(client, test_token)
    headers = auth_headers(test_token)
    await client.put(
        f"{API}/repos/testuser/refs-repo/contents/.github/workflows/sync.yml", headers=headers,
        json={"message": "add workflow", "content": base64.b64encode(WORKFLOW.encode()).decode(), "branch": "main"},
    )
    main = await _head(client, test_token, "refs-repo", "main")
    first = await _commit(client, test_token, "refs-repo", main, "a.txt", "a\n")
    assert "errors" not in await _update(client, test_token, repo_id, ("refs/heads/feature", ZERO, first, False))
    pull = await client.post(f"{API}/repos/testuser/refs-repo/pulls", headers=headers,
                             json={"title": "feature", "head": "feature", "base": "main"})
    assert pull.status_code == 201, pull.text
    second = await _commit(client, test_token, "refs-repo", first, "b.txt", "b\n")

    # What receive-pack leaves behind, then the post-push work it schedules.
    repository = (await db_session.execute(select(Repository).where(Repository.full_name == "testuser/refs-repo"))).scalar_one()
    subprocess.run(["git", "update-ref", "refs/heads/feature", second, first],
                   env={**os.environ, "GIT_DIR": repository.disk_path}, check=True)
    await smart_http._post_receive_pack_tasks(repository.id, test_user.id)

    runs = (await db_session.execute(select(WorkflowRun).where(WorkflowRun.event == "pull_request_target"))).scalars().all()
    assert len(runs) == 1
    refreshed = (await client.get(f"{API}/repos/testuser/refs-repo/pulls/{pull.json()['number']}", headers=headers)).json()
    assert refreshed["head"]["sha"] == second


async def test_an_unchanged_ref_is_still_checked_when_the_batch_applies(client, test_user, test_token, db_session, monkeypatch):
    """A ref updated to its own value takes no part in the change, but its
    beforeOid must still hold at the moment the others apply."""
    import os
    import subprocess

    from app.models.repository import Repository
    from app.services import ref_update_service

    repo_id, main = await _repo(client, test_token)
    first = await _commit(client, test_token, "refs-repo", main, "a.txt", "a\n")
    repository = (await db_session.execute(select(Repository).where(Repository.full_name == "testuser/refs-repo"))).scalar_one()
    real_apply = ref_update_service.apply

    async def racing_apply(repo_path, planned):
        # Another writer moves main after the checks, before the transaction.
        subprocess.run(["git", "update-ref", "refs/heads/main", first], env={**os.environ, "GIT_DIR": repo_path}, check=True)
        await real_apply(repo_path, planned)

    monkeypatch.setattr(ref_update_service, "apply", racing_apply)
    raced = await _update(client, test_token, repo_id,
                          ("refs/heads/main", main, main, False), ("refs/heads/other", ZERO, main, False))
    assert _error(raced)[0] == "STALE_DATA"
    assert await _head(client, test_token, "refs-repo", "other") is None
    assert repository.disk_path


async def test_a_dispatch_failure_still_records_pull_request_heads(client, test_user, test_token, monkeypatch):
    import app.services.workflow_service as workflow_service

    repo_id, main = await _repo(client, test_token)
    headers = auth_headers(test_token)
    first = await _commit(client, test_token, "refs-repo", main, "a.txt", "a\n")
    assert "errors" not in await _update(client, test_token, repo_id, ("refs/heads/feature", ZERO, first, False))
    pull = await client.post(f"{API}/repos/testuser/refs-repo/pulls", headers=headers,
                             json={"title": "feature", "head": "feature", "base": "main"})
    second = await _commit(client, test_token, "refs-repo", first, "b.txt", "b\n")

    async def broken(*args, **kwargs):
        raise RuntimeError("dispatch failed")

    monkeypatch.setattr(workflow_service, "process_push_event", broken)
    moved = await _update(client, test_token, repo_id, ("refs/heads/feature", first, second, False))
    assert "errors" not in moved, moved
    refreshed = (await client.get(f"{API}/repos/testuser/refs-repo/pulls/{pull.json()['number']}", headers=headers)).json()
    assert refreshed["head"]["sha"] == second
