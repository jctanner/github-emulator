"""Compare, label and request-body edge cases, each as GitHub answers it.

- Compare reads repository contents, so an App or job token needs
  ``contents: read`` for it.
- A fork side is compared at the commit that was actually fetched.
- Unpaged, compare lists the most recent 250 commits.
- ``files`` keep any file name exactly and carry each blob's sha.
- ``labels`` must be an array.
- A JSON body nested past the decoder's limit is a client error.
- A label create that loses a race to the unique index answers
  ``already_exists``.
- A fork the caller cannot read is not compared, and a contents write that
  loses a race to another commit answers 409.
"""

import base64
import subprocess
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from sqlalchemy import select

from app.models.repository import Repository
from tests.conftest import API, auth_headers
from tests.test_compare_api import _branch, _main_sha, _put
from tests.test_installation_administration import _bot_token, org  # noqa: F401

pytestmark = pytest.mark.asyncio


def _git(git_dir, *args, env=None):
    return subprocess.run(
        ["git", "--git-dir", git_dir, *args], capture_output=True, text=True, check=True, env=env,
    ).stdout.strip()


def _commit(git_dir, parent, message):
    """A commit on ``parent``'s tree (or an empty tree), made with plumbing."""
    import os

    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    tree = _git(git_dir, "rev-parse", f"{parent}^{{tree}}") if parent else _git(git_dir, "mktree", env=env)
    args = ["commit-tree", tree, "-m", message] + (["-p", parent] if parent else [])
    return _git(git_dir, *args, env=env)


async def _disk_path(db_session, full_name):
    return (await db_session.execute(select(Repository).where(Repository.full_name == full_name))).scalar_one().disk_path


# 1. Compare needs contents: read.

async def test_compare_needs_the_tokens_contents_permission(client, org, admin_token, test_token):  # noqa: F811
    created = await client.post(
        f"{API}/orgs/{org}/repos", json={"name": "secret-src", "private": True, "auto_init": True},
        headers=auth_headers(test_token),
    )
    assert created.status_code == 201, created.text
    url = f"{API}/repos/{org}/secret-src/compare/main...main"

    metadata_only = await _bot_token(client, admin_token, "4001", "meta-only", {"metadata": "read"})
    refused = await client.get(url, headers=metadata_only)
    assert refused.status_code == 403, refused.text
    assert "Resource not accessible by integration" in refused.json()["message"]

    readers = await _bot_token(client, admin_token, "4002", "contents-read", {"contents": "read", "metadata": "read"})
    assert (await client.get(url, headers=readers)).status_code == 200


def test_a_job_token_needs_contents_read_for_compare():
    from app.services import job_permissions

    path = "/api/v3/repos/o/r/compare/main...feature"
    assert job_permissions.check("GET", path, {"metadata": "read"}) is not None
    assert job_permissions.check("GET", path, {"contents": "read"}) is None


# 2. The fork side is the commit that was fetched.

async def test_a_fork_branch_that_moves_mid_fetch_is_compared_where_it_was_fetched(tmp_path, monkeypatch):
    """The head branch is force-pushed to an unrelated commit just before the
    fetch runs. The commit returned must be the one fetched, which the base
    repository then has."""
    import asyncio

    from app.services import cross_repo_pulls

    base_dir, head_dir = str(tmp_path / "base.git"), str(tmp_path / "head.git")
    for git_dir in (base_dir, head_dir):
        subprocess.run(["git", "init", "--bare", "-q", git_dir], check=True)
    first = _commit(head_dir, None, "first")
    _git(head_dir, "update-ref", "refs/heads/topic", first)
    rewritten = _commit(head_dir, None, "rewritten")

    real_exec = asyncio.create_subprocess_exec

    async def exec_moving_the_branch(*args, **kwargs):
        if "fetch" in args:
            _git(head_dir, "update-ref", "refs/heads/topic", rewritten)
        return await real_exec(*args, **kwargs)

    monkeypatch.setattr(cross_repo_pulls.asyncio, "create_subprocess_exec", exec_moving_the_branch)
    sha = await cross_repo_pulls.fetch_branch_objects(
        SimpleNamespace(disk_path=base_dir), SimpleNamespace(disk_path=head_dir), "topic"
    )

    assert sha == rewritten
    _git(base_dir, "cat-file", "-e", f"{sha}^{{commit}}")
    # The temporary ref is gone; only the objects stay.
    assert _git(base_dir, "for-each-ref", "refs/emulator") == ""


# 3. Unpaged, the most recent 250 commits.

async def test_an_unpaged_compare_lists_the_most_recent_250_commits(client, db_session, test_token):
    await client.post(f"{API}/user/repos", json={"name": "long-compare", "auto_init": True}, headers=auth_headers(test_token))
    git_dir = await _disk_path(db_session, "testuser/long-compare")
    main = _git(git_dir, "rev-parse", "refs/heads/main")
    shas, parent = [], main
    for n in range(260):
        parent = _commit(git_dir, parent, f"c{n}")
        shas.append(parent)
    _git(git_dir, "update-ref", "refs/heads/long", parent)

    resp = await client.get(f"{API}/repos/testuser/long-compare/compare/main...long", headers=auth_headers(test_token))

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_commits"] == 260
    assert [c["sha"] for c in data["commits"]] == shas[-250:]


# 4. Files keep their names and blob shas.

async def test_compare_files_keep_unicode_names_and_blob_shas(client, test_token):
    headers = auth_headers(test_token)
    await client.post(f"{API}/user/repos", json={"name": "named-files", "auto_init": True}, headers=headers)
    main = await _main_sha(client, test_token, "testuser/named-files")
    await _branch(client, test_token, "testuser/named-files", "names", main)
    expected = {}
    for name in ("café.txt", "dir with space/a b.txt"):
        put = await client.put(
            f"{API}/repos/testuser/named-files/contents/{quote(name)}", headers=headers,
            json={"message": f"add {name}", "content": base64.b64encode(b"x\n").decode(), "branch": "names"},
        )
        assert put.status_code == 201, put.text
        expected[name] = put.json()["content"]["sha"]

    resp = await client.get(f"{API}/repos/testuser/named-files/compare/main...names", headers=headers)

    files = {f["filename"]: f for f in resp.json()["files"]}
    assert set(files) == set(expected)
    for name, sha in expected.items():
        assert files[name]["sha"] == sha
        assert files[name]["status"] == "added"
        assert files[name]["additions"] == 1
        assert files[name]["patch"].startswith("@@")
        assert files[name]["raw_url"].endswith(quote(name))


# 5. labels must be an array.

async def test_a_bare_string_labels_is_refused(client, test_token):
    headers = auth_headers(test_token)
    await client.post(f"{API}/user/repos", json={"name": "label-shape"}, headers=headers)

    resp = await client.post(f"{API}/repos/testuser/label-shape/issues", json={"title": "t", "labels": "bug"}, headers=headers)

    assert resp.status_code == 422, resp.text
    assert "is not an array" in resp.json()["message"]
    listed = await client.get(f"{API}/repos/testuser/label-shape/labels", headers=headers)
    assert listed.json() == []


# 6. Deep JSON is a client error.

async def test_a_deeply_nested_body_is_a_client_error(client, test_token):
    headers = {**auth_headers(test_token), "content-type": "text/plain"}
    await client.post(f"{API}/user/repos", json={"name": "deep-body"}, headers=auth_headers(test_token))

    resp = await client.post(f"{API}/repos/testuser/deep-body/issues", content=b"[" * 200_000, headers=headers)

    assert 400 <= resp.status_code < 500, resp.status_code


# Follow-ups.

async def test_a_label_create_that_loses_the_race_reports_already_exists(client, test_token, monkeypatch):
    """The name check reads first; when another create took the name in
    between, the unique index refuses this one, which is already_exists."""
    from app.api import labels

    headers = auth_headers(test_token)
    await client.post(f"{API}/user/repos", json={"name": "label-race"}, headers=headers)
    assert (await client.post(f"{API}/repos/testuser/label-race/labels", json={"name": "bug", "color": "ededed"},
                              headers=headers)).status_code == 201

    async def missed(*_args, **_kwargs):
        return None

    monkeypatch.setattr(labels, "_find_label", missed)
    resp = await client.post(f"{API}/repos/testuser/label-race/labels", json={"name": "bug", "color": "ededed"},
                             headers=headers)

    assert resp.status_code == 422, resp.text
    assert resp.json()["errors"] == [{"resource": "Label", "code": "already_exists", "field": "name"}]


async def test_a_fork_the_caller_cannot_read_is_not_compared(client, db_session, test_token, admin_token):
    """The fork side is compared only when the caller can read the fork."""
    admin = auth_headers(admin_token)
    created = await client.post(f"{API}/user/repos", json={"name": "upstream", "auto_init": True}, headers=auth_headers(test_token))
    assert created.status_code == 201, created.text
    fork = await client.post(f"{API}/repos/testuser/upstream/forks", json={}, headers=admin)
    assert fork.status_code == 202, fork.text
    main = await _main_sha(client, admin_token, "admin/upstream")
    await _branch(client, admin_token, "admin/upstream", "hidden", main)
    await _put(client, admin_token, "admin/upstream", "h.txt", "h\n", "hidden")
    made_private = await client.patch(f"{API}/repos/admin/upstream", json={"private": True}, headers=admin)
    assert made_private.status_code == 200, made_private.text
    assert (await client.get(f"{API}/repos/admin/upstream", headers=auth_headers(test_token))).status_code == 404

    resp = await client.get(f"{API}/repos/testuser/upstream/compare/main...admin:hidden", headers=auth_headers(test_token))

    assert resp.status_code == 404
    seen = await client.get(f"{API}/repos/testuser/upstream/compare/main...admin:hidden", headers=admin)
    assert seen.status_code == 200, seen.text


async def test_a_contents_write_that_loses_the_race_answers_409(client, db_session, test_token, monkeypatch):
    """Another commit lands on the branch between this write's sha check and
    its branch update. The compare-and-swap update refuses, so the other
    commit stays and this write answers 409."""
    from app.api import contents

    headers = auth_headers(test_token)
    await client.post(f"{API}/user/repos", json={"name": "cas", "auto_init": True}, headers=headers)
    git_dir = await _disk_path(db_session, "testuser/cas")
    readme = await client.get(f"{API}/repos/testuser/cas/contents/README.md", headers=headers)
    blob_sha = readme.json()["sha"]
    other = {}
    real_git = contents._git

    async def git_with_a_concurrent_commit(repo_path, *args, **kwargs):
        if args and args[0] == "update-ref" and not other:
            other["sha"] = _commit(git_dir, _git(git_dir, "rev-parse", "refs/heads/main"), "concurrent")
            _git(git_dir, "update-ref", "refs/heads/main", other["sha"])
        return await real_git(repo_path, *args, **kwargs)

    monkeypatch.setattr(contents, "_git", git_with_a_concurrent_commit)
    resp = await client.put(
        f"{API}/repos/testuser/cas/contents/README.md", headers=headers,
        json={"message": "edit", "content": base64.b64encode(b"new\n").decode(), "sha": blob_sha, "branch": "main"},
    )

    assert resp.status_code == 409, resp.text
    assert _git(git_dir, "rev-parse", "refs/heads/main") == other["sha"]


# Commit messages keep their body.

async def test_compare_commits_carry_the_full_message(client, db_session, test_token):
    import os

    headers = auth_headers(test_token)
    await client.post(f"{API}/user/repos", json={"name": "long-messages", "auto_init": True}, headers=headers)
    git_dir = await _disk_path(db_session, "testuser/long-messages")
    main = _git(git_dir, "rev-parse", "refs/heads/main")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    message = "Subject line\n\nBody paragraph one.\n\nBody paragraph two."
    commit = _git(git_dir, "commit-tree", f"{main}^{{tree}}", "-p", main, "-m", message, env=env)
    _git(git_dir, "update-ref", "refs/heads/described", commit)

    resp = await client.get(f"{API}/repos/testuser/long-messages/compare/main...described", headers=headers)

    assert resp.status_code == 200, resp.text
    assert [c["commit"]["message"] for c in resp.json()["commits"]] == [message]


# A file name that is not UTF-8 does not fail the compare.

async def test_a_non_utf8_file_name_is_compared(client, db_session, test_token):
    import os

    headers = auth_headers(test_token)
    await client.post(f"{API}/user/repos", json={"name": "latin1-names", "auto_init": True}, headers=headers)
    git_dir = await _disk_path(db_session, "testuser/latin1-names")
    main = _git(git_dir, "rev-parse", "refs/heads/main")
    blob = subprocess.run(["git", "--git-dir", git_dir, "hash-object", "-w", "--stdin"], input=b"x\n",
                          capture_output=True, check=True).stdout.decode().strip()
    listing = subprocess.run(["git", "--git-dir", git_dir, "ls-tree", "-z", main], capture_output=True, check=True).stdout
    name = b"caf\xe9.txt"  # Latin-1, not UTF-8
    tree = subprocess.run(["git", "--git-dir", git_dir, "mktree", "-z"], input=listing + b"100644 blob " + blob.encode() + b"\t" + name + b"\0",
                          capture_output=True, check=True).stdout.decode().strip()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    commit = _git(git_dir, "commit-tree", tree, "-p", main, "-m", "latin-1 name", env=env)
    _git(git_dir, "update-ref", "refs/heads/latin1", commit)

    resp = await client.get(f"{API}/repos/testuser/latin1-names/compare/main...latin1", headers=headers)

    assert resp.status_code == 200, resp.text
    (changed,) = resp.json()["files"]
    assert changed["filename"] == "caf�.txt"
    assert changed["sha"] == blob
    assert changed["raw_url"].endswith("caf%E9.txt")
    assert "path_bytes" not in changed
