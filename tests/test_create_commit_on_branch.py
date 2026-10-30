"""GraphQL createCommitOnBranch, in the shape Fullsend's scaffold commits use."""

import base64
import subprocess

from sqlalchemy import select

from app.models.repository import Repository

from tests.conftest import API, auth_headers
from tests.test_org_memberships_api import _make_user

MUTATION = """mutation($input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $input) {
    commit { oid }
  }
}"""


async def _repo(client, token, name="commit-target"):
    resp = await client.post(f"{API}/user/repos", json={"name": name, "auto_init": True},
                             headers=auth_headers(token))
    assert resp.status_code == 201, resp.text
    head = (await client.get(f"{API}/repos/testuser/{name}/branches/main")).json()["commit"]["sha"]
    return name, head


def _input(name, head, additions=(), deletions=(), headline="Add files", body=None, branch="main"):
    message = {"headline": headline}
    if body:
        message["body"] = body
    return {"input": {
        "branch": {"repositoryNameWithOwner": f"testuser/{name}", "branchName": branch},
        "expectedHeadOid": head,
        "message": message,
        "fileChanges": {
            "additions": [{"path": p, "contents": base64.b64encode(c).decode()} for p, c in additions],
            "deletions": [{"path": p} for p in deletions],
        },
    }}


async def _commit(client, token, variables, path="/api/graphql"):
    resp = await client.post(path, json={"query": MUTATION, "variables": variables},
                             headers=auth_headers(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _file(client, name, path):
    resp = await client.get(f"{API}/repos/testuser/{name}/contents/{path}")
    return base64.b64decode(resp.json()["content"]) if resp.status_code == 200 else None


async def test_commits_additions_and_deletions_atomically(client, db_session, test_token):
    name, head = await _repo(client, test_token)
    first = await _commit(client, test_token, _input(name, head, additions=[
        (".github/workflows/fullsend.yaml", b"on: issues\n"), ("docs/a.md", b"a\n")]))
    oid = first["data"]["createCommitOnBranch"]["commit"]["oid"]
    assert "errors" not in first and len(oid) == 40
    branch = (await client.get(f"{API}/repos/testuser/{name}/branches/main")).json()
    assert branch["commit"]["sha"] == oid
    assert await _file(client, name, ".github/workflows/fullsend.yaml") == b"on: issues\n"

    second = await _commit(client, test_token, _input(
        name, oid, additions=[("docs/b.md", b"b\n")], deletions=["docs/a.md"],
        headline="Rework docs", body="Body line\n\nSigned-off-by: T <t@t>"))
    new = second["data"]["createCommitOnBranch"]["commit"]["oid"]
    assert await _file(client, name, "docs/a.md") is None
    assert await _file(client, name, "docs/b.md") == b"b\n"
    commit = (await client.get(f"{API}/repos/testuser/{name}/git/commits/{new}")).json()
    assert commit["parents"][0]["sha"] == oid
    assert commit["committer"]["name"] == "GitHub"
    # The REST git/commits endpoint returns only the subject, so read the
    # full message from the object itself.
    repository = (await db_session.execute(
        select(Repository).where(Repository.full_name == f"testuser/{name}"))).scalar_one()
    raw = subprocess.run(["git", "--git-dir", repository.disk_path, "log", "-1", "--format=%B", new],
                         capture_output=True, text=True, check=True).stdout
    assert raw.rstrip("\n") == "Rework docs\n\nBody line\n\nSigned-off-by: T <t@t>"


async def test_stale_head_is_reported_as_github_does(client, test_token):
    name, head = await _repo(client, test_token)
    await _commit(client, test_token, _input(name, head, additions=[("x.txt", b"1\n")]))
    stale = await _commit(client, test_token, _input(name, head, additions=[("x.txt", b"2\n")]))
    error = stale["errors"][0]
    # Fullsend matches the type or the "is at <sha> but expected <sha>" text.
    assert error["type"] == "STALE_DATA"
    assert f"but expected {head}" in error["message"]
    assert await _file(client, name, "x.txt") == b"1\n"


async def test_missing_repository_and_branch_are_not_found(client, test_token):
    name, head = await _repo(client, test_token)
    missing = _input("does-not-exist", head, additions=[("x", b"x")])
    assert (await _commit(client, test_token, missing))["errors"][0]["type"] == "NOT_FOUND"
    no_branch = _input(name, head, additions=[("x", b"x")], branch="nope")
    assert (await _commit(client, test_token, no_branch))["errors"][0]["type"] == "NOT_FOUND"


async def test_deleting_a_missing_path_aborts_the_commit(client, test_token):
    name, head = await _repo(client, test_token)
    result = await _commit(client, test_token, _input(
        name, head, additions=[("new.txt", b"n\n")], deletions=["absent.txt"]))
    assert result["errors"][0]["type"] == "UNPROCESSABLE"
    branch = (await client.get(f"{API}/repos/testuser/{name}/branches/main")).json()
    assert branch["commit"]["sha"] == head
    assert await _file(client, name, "new.txt") is None


async def test_requires_write_access(client, db_session, test_token):
    name, head = await _repo(client, test_token)
    outsider = await _make_user(db_session, "outsider")
    result = await _commit(client, outsider, _input(name, head, additions=[("x", b"x")]))
    assert result["errors"][0]["type"] == "FORBIDDEN"
    branch = (await client.get(f"{API}/repos/testuser/{name}/branches/main")).json()
    assert branch["commit"]["sha"] == head
