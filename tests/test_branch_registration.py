"""Repository writers must expose their on-disk branches through REST."""

import asyncio
import base64

import pytest
from sqlalchemy import select

from app.models.repository import Repository
from tests.conftest import API, auth_headers

pytestmark = pytest.mark.asyncio


async def _disk_sha(db, full_name, branch):
    repository = (await db.execute(
        select(Repository).where(Repository.full_name == full_name)
    )).scalar_one()
    proc = await asyncio.create_subprocess_exec(
        "git", "--git-dir", repository.disk_path,
        "rev-parse", f"refs/heads/{branch}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    output, error = await proc.communicate()
    assert proc.returncode == 0, error.decode()
    return output.decode().strip()


async def _assert_branch(client, full_name, branch, sha):
    root = f"{API}/repos/{full_name}"
    response = await client.get(root + "/branches")
    assert response.status_code == 200
    assert {b["name"]: b["commit"]["sha"] for b in response.json()}[branch] == sha
    response = await client.get(root + f"/branches/{branch}")
    assert response.status_code == 200, response.text
    assert response.json()["commit"]["sha"] == sha
    response = await client.get(root + f"/git/ref/heads/{branch}")
    assert response.status_code == 200, response.text
    assert response.json()["object"]["sha"] == sha
    response = await client.get(root + "/git/matching-refs/heads")
    assert response.status_code == 200, response.text
    assert {r["ref"]: r["object"]["sha"] for r in response.json()}[f"refs/heads/{branch}"] == sha


@pytest.mark.parametrize("organization", [False, True])
@pytest.mark.parametrize("branch", ["main", "release/v1"])
async def test_auto_init_registers_default_branch(client, db_session, test_user, test_token, organization, branch):
    headers = auth_headers(test_token)
    namespace, endpoint = "testuser", "/user/repos"
    if organization:
        response = await client.post(f"{API}/orgs", json={"login": "branch-org"}, headers=headers)
        assert response.status_code == 201
        namespace, endpoint = "branch-org", "/orgs/branch-org/repos"
    response = await client.post(API + endpoint, json={"name": "initialized", "auto_init": True, "default_branch": branch}, headers=headers)
    assert response.status_code == 201, response.text
    assert response.json()["default_branch"] == branch
    full_name = f"{namespace}/initialized"
    sha = await _disk_sha(db_session, full_name, branch)
    await _assert_branch(client, full_name, branch, sha)


async def test_fork_registers_all_disk_branches(client, db_session, test_user, test_token, admin_user, admin_token):
    headers = auth_headers(test_token)
    response = await client.post(f"{API}/user/repos", json={"name": "source", "auto_init": True}, headers=headers)
    assert response.status_code == 201
    sha = await _disk_sha(db_session, "testuser/source", "main")
    response = await client.post(f"{API}/repos/testuser/source/git/refs", json={"ref": "refs/heads/release/v1", "sha": sha}, headers=headers)
    assert response.status_code == 201
    response = await client.post(f"{API}/repos/testuser/source/forks", json={}, headers=auth_headers(admin_token))
    assert response.status_code == 202, response.text
    for branch in ["main", "release/v1"]:
        await _assert_branch(client, "admin/source", branch, sha)


async def test_contents_registers_first_commit_and_updates_branch(client, db_session, test_user, test_token):
    headers = auth_headers(test_token)
    response = await client.post(f"{API}/user/repos", json={"name": "empty"}, headers=headers)
    assert response.status_code == 201
    root = f"{API}/repos/testuser/empty"
    assert (await client.get(root + "/branches")).json() == []
    for branch in ["main", "main", "other"]:
        response = await client.put(root + f"/contents/{branch}.txt", json={"message": "Write file", "branch": branch, "content": base64.b64encode(f"{branch} content".encode()).decode()}, headers=headers)
        assert response.status_code in (200, 201), response.text
        sha = await _disk_sha(db_session, "testuser/empty", branch)
        assert response.json()["commit"]["sha"] == sha
        await _assert_branch(client, "testuser/empty", branch, sha)
    branches = (await client.get(root + "/branches")).json()
    assert [b["name"] for b in branches] == ["main", "other"]
