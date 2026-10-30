"""Aggregate PR diff downloads, media negotiation, and scoped access."""

import asyncio
import os

import pytest
from sqlalchemy import select, update

from app.git.bare_repo import write_file
from app.models.actions import Workflow, WorkflowJob, WorkflowRun
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.services.job_token_service import issue_job_token
from tests.conftest import API, auth_headers
from tests.test_installation_token_permissions import _app, _bearer, _mint
from tests.test_private_repository_access import outsider_token  # noqa: F401
from tests.test_pulls_api import _create_real_pr_with_diff

pytestmark = pytest.mark.asyncio

REPRESENTATIONS = ["ui", "web", "api", "api-v3"]


def _request(repo_name, representation, number=1):
    full_name = f"testuser/{repo_name}"
    if representation == "ui":
        return f"/ui/{full_name}/pulls/{number}.diff", {}
    if representation == "web":
        return f"/{full_name}/pull/{number}.diff", {}
    media = "application/vnd.github.diff"
    if representation == "api-v3":
        media = "application/vnd.github.v3.diff"
    return f"{API}/repos/{full_name}/pulls/{number}", {"Accept": media}


async def _git(disk_path, *args):
    proc = await asyncio.create_subprocess_exec(
        "git", *args, env={**os.environ, "GIT_DIR": disk_path},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    output, error = await proc.communicate()
    assert proc.returncode == 0, error.decode()
    return output


@pytest.fixture
async def diff_repo(client, db_session, test_token):
    name = await _create_real_pr_with_diff(client, db_session, test_token)
    return (await db_session.execute(
        select(Repository).where(Repository.full_name == f"testuser/{name}")
    )).scalar_one()


@pytest.mark.parametrize("representation", REPRESENTATIONS)
async def test_diff_is_aggregate_git_output_from_merge_base(
    client, diff_repo, representation,
):
    # Both branches advance: only the PR's net changes belong in the diff.
    for branch, path, content in [
        ("main", "base-only.txt", b"base change\n"),
        ("feature", "feature.txt", b"final feature content\n"),
        ("feature", "second.txt", b"second file\n"),
    ]:
        await write_file(
            diff_repo.disk_path, branch, path, content, "Update file",
            "Test User", "test@test.com",
        )
    expected = await _git(
        diff_repo.disk_path, "diff", "--find-renames", "--patch", "main...feature", "--",
    )
    url, headers = _request(diff_repo.name, representation)
    response = await client.get(url, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.content == expected
    assert response.content.count(b"diff --git ") == 2
    assert b"+final feature content" in response.content
    assert b"base-only.txt" not in response.content
    assert b"hello from a pull request" not in response.content

    head = await client.head(url, headers=headers)
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(expected))


async def test_raw_diff_preserves_rename_mode_binary_and_deletion_metadata(
    client, diff_repo, tmp_path,
):
    work = tmp_path / "work"
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--branch", "feature", diff_repo.disk_path, str(work),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, error = await proc.communicate()
    assert proc.returncode == 0, error.decode()
    (work / "README.md").rename(work / "renamed.md")
    (work / "feature.txt").unlink()
    (work / "run.sh").write_bytes(b"#!/bin/sh\nexit 0")
    (work / "run.sh").chmod(0o755)
    (work / "data.bin").write_bytes(b"\x00\xff\x01")
    git_dir = str(work / ".git")
    # Explicit work tree since this test's Git helper always sets GIT_DIR.
    await _git(git_dir, "--work-tree=" + str(work), "add", "-A")
    await _git(
        git_dir, "-c", "user.name=Test", "-c", "user.email=test@test.com",
        "commit", "-m", "Rename, remove, and add files",
    )
    await _git(git_dir, "push", "origin", "feature")
    # The deleted feature file was added earlier in this PR, so it disappears
    # from the aggregate diff. Delete a base file as well to test deletion.
    await write_file(
        diff_repo.disk_path, "main", "delete.txt", b"remove me\n", "Add base file",
        "Test User", "test@test.com",
    )
    # Include that base commit in the head, then remove the base file.
    await _git(git_dir, "fetch", "origin", "main")
    await _git(
        git_dir, "--work-tree=" + str(work), "-c", "user.name=Test",
        "-c", "user.email=test@test.com", "merge", "--no-edit", "origin/main",
    )
    (work / "delete.txt").unlink()
    await _git(git_dir, "--work-tree=" + str(work), "add", "-A")
    await _git(
        git_dir, "-c", "user.name=Test", "-c", "user.email=test@test.com",
        "commit", "-m", "Delete base file",
    )
    await _git(git_dir, "push", "origin", "feature")
    url, headers = _request(diff_repo.name, "ui")
    response = await client.get(url, headers=headers)
    assert response.status_code == 200
    assert b"rename from README.md\nrename to renamed.md\n" in response.content
    assert b"new file mode 100755" in response.content
    assert b"deleted file mode 100644" in response.content
    assert b"Binary files /dev/null and b/data.bin differ" in response.content
    assert b"\\ No newline at end of file" in response.content
    assert b"feature.txt" not in response.content
    assert response.content == await _git(
        diff_repo.disk_path, "diff", "--find-renames", "--patch", "main...feature", "--",
    )


@pytest.mark.parametrize("representation", REPRESENTATIONS)
async def test_empty_diff_is_success(client, diff_repo, representation):
    await _git(diff_repo.disk_path, "update-ref", "refs/heads/feature", "refs/heads/main")
    url, headers = _request(diff_repo.name, representation)
    response = await client.get(url, headers=headers)
    assert response.status_code == 200
    assert response.content == b""


@pytest.mark.parametrize("representation", REPRESENTATIONS)
async def test_missing_pr_repository_and_git_objects_are_404(
    client, db_session, diff_repo, representation,
):
    for name, number in [(diff_repo.name, 999), ("missing-repo", 1)]:
        url, headers = _request(name, representation, number)
        assert (await client.get(url, headers=headers)).status_code == 404
    # Neither a live ref nor a saved SHA resolves; do not return a fake empty diff.
    await db_session.execute(update(PullRequest).values(head_ref="absent", head_sha="0" * 40))
    await db_session.commit()
    url, headers = _request(diff_repo.name, representation)
    assert (await client.get(url, headers=headers)).status_code == 404


async def test_deleted_head_branch_and_merged_pr_retain_diff(
    client, db_session, diff_repo, test_token,
):
    # Advance both refs after PR creation; the merge must save their final SHAs.
    for branch, path in [("main", "base-later.txt"), ("feature", "head-later.txt")]:
        await write_file(
            diff_repo.disk_path, branch, path, b"later\n", "Advance branch",
            "Test User", "test@test.com",
        )
    url, _ = _request(diff_repo.name, "ui")
    original = await client.get(url)
    merged = await client.put(
        f"{API}/repos/{diff_repo.full_name}/pulls/1/merge", json={},
        headers=auth_headers(test_token),
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["merged"] is True
    await _git(diff_repo.disk_path, "update-ref", "-d", "refs/heads/feature")
    response = await client.get(url)
    assert response.status_code == 200
    assert response.content == original.content


async def test_closed_unmerged_pr_with_deleted_branch_retains_diff(
    client, diff_repo, test_token,
):
    url, _ = _request(diff_repo.name, "web")
    original = await client.get(url)
    closed = await client.patch(
        f"{API}/repos/{diff_repo.full_name}/pulls/1", json={"state": "closed"},
        headers=auth_headers(test_token),
    )
    assert closed.status_code == 200
    await _git(diff_repo.disk_path, "update-ref", "-d", "refs/heads/feature")
    response = await client.get(url)
    assert response.status_code == 200
    assert response.content == original.content


async def test_missing_git_repository_is_404(client, db_session, diff_repo):
    diff_repo.disk_path = "/nonexistent/pr-diff-test.git"
    await db_session.commit()
    url, _ = _request(diff_repo.name, "ui")
    assert (await client.get(url)).status_code == 404


async def test_private_diff_accepts_browser_session(
    client, db_session, diff_repo, test_user,
):
    from app.services.auth_service import hash_password

    diff_repo.private = True
    test_user.hashed_password = hash_password("password")
    await db_session.commit()
    login = await client.post(
        "/api/_ui/session", json={"username": "testuser", "password": "password"},
    )
    assert login.status_code == 200
    url, _ = _request(diff_repo.name, "ui")
    response = await client.get(url)
    assert response.status_code == 200
    assert b"+hello from a pull request" in response.content


@pytest.mark.parametrize("accept", [
    "application/vnd.github+json", "*/*", "text/html",
    "application/vnd.github.diff;q=0, application/vnd.github+json",
])
async def test_json_remains_default(client, diff_repo, accept):
    response = await client.get(
        f"{API}/repos/{diff_repo.full_name}/pulls/1", headers={"Accept": accept},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["vary"] == "Accept"
    assert response.json()["number"] == 1
    assert response.json()["changed_files"] == 1
    advertised = await client.get(response.json()["diff_url"])
    assert advertised.status_code == 200
    assert b"+hello from a pull request" in advertised.content


@pytest.mark.parametrize("representation", REPRESENTATIONS)
async def test_private_diff_requires_repository_access(
    client, db_session, diff_repo, test_token, outsider_token, representation,
):
    diff_repo.private = True
    await db_session.commit()
    url, headers = _request(diff_repo.name, representation)
    for auth in ({}, auth_headers(outsider_token)):
        assert (await client.get(url, headers={**headers, **auth})).status_code == 404
    owner = await client.get(url, headers={**headers, **auth_headers(test_token)})
    assert owner.status_code == 200


@pytest.mark.parametrize("representation", REPRESENTATIONS)
async def test_installation_diff_respects_permissions_and_repository_selection(
    client, diff_repo, admin_token, representation,
):
    installation_id, app_headers = await _app(
        client, admin_token, "4100", "diff-reader",
        {"pull_requests": "read", "contents": "read", "metadata": "read"}, [],
    )
    url, headers = _request(diff_repo.name, representation)
    for repositories, permissions, expected_status in [
        ([diff_repo.name], {"pull_requests": "read"}, 200),
        ([diff_repo.name], {"contents": "read"}, 403),
        (["other-repository"], {"pull_requests": "read"}, 404),
    ]:
        minted = await _mint(client, installation_id, app_headers, repositories, permissions)
        assert minted.status_code == 201, minted.text
        response = await client.get(url, headers={**headers, **_bearer(minted)})
        assert response.status_code == expected_status, response.text


@pytest.mark.parametrize("representation", REPRESENTATIONS)
async def test_job_diff_requires_pull_request_permission(
    client, db_session, diff_repo, test_user, representation,
):
    workflow = Workflow(repo_id=diff_repo.id, name="Diff", path=".github/workflows/diff.yml")
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id, repo_id=diff_repo.id, head_sha="abc",
        head_branch="main", event="workflow_dispatch", run_number=1,
        actor_id=test_user.id,
    )
    db_session.add(run)
    await db_session.flush()
    job = WorkflowJob(run_id=run.id, name="diff", permissions={"contents": "read"})
    db_session.add(job)
    await db_session.commit()
    url, headers = _request(diff_repo.name, representation)
    headers["Authorization"] = f"Bearer {issue_job_token(job)}"
    assert (await client.get(url, headers=headers)).status_code == 403
    job.permissions = {"pull-requests": "read"}
    await db_session.commit()
    assert (await client.get(url, headers=headers)).status_code == 200
