"""Tests for the Git Smart HTTP protocol endpoints.

These tests verify that the info/refs, upload-pack, and receive-pack
endpoints respond correctly. Full git clone/push integration requires
a running server; these tests validate the HTTP-level behavior.
"""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


@pytest.fixture
async def git_repo(client, test_user, test_token, tmp_path):
    """Create a repo with auto_init so it has a bare git directory."""
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "git-test", "auto_init": True},
        headers=auth_headers(test_token),
    )
    return resp.json()


@pytest.mark.asyncio
async def test_info_refs_upload_pack(client, test_user, test_token, git_repo):
    """GET /{owner}/{repo}.git/info/refs?service=git-upload-pack returns refs."""
    resp = await client.get(
        "/testuser/git-test.git/info/refs?service=git-upload-pack"
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-git-upload-pack-advertisement"
    # Response should contain pkt-line service announcement
    body = resp.content
    assert b"# service=git-upload-pack" in body


@pytest.mark.asyncio
async def test_info_refs_receive_pack_requires_auth(client, test_user, test_token, git_repo):
    """GET info/refs?service=git-receive-pack without auth returns 401."""
    resp = await client.get(
        "/testuser/git-test.git/info/refs?service=git-receive-pack"
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_info_refs_receive_pack_with_auth(client, test_user, test_token, git_repo):
    """GET info/refs?service=git-receive-pack with auth succeeds."""
    resp = await client.get(
        "/testuser/git-test.git/info/refs?service=git-receive-pack",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-git-receive-pack-advertisement"
    assert b"# service=git-receive-pack" in resp.content


@pytest.mark.asyncio
async def test_info_refs_invalid_service(client, test_user, test_token, git_repo):
    """GET info/refs with invalid service returns 403."""
    resp = await client.get(
        "/testuser/git-test.git/info/refs?service=invalid-service"
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_info_refs_without_git_suffix(client, test_user, test_token, git_repo):
    """GET /{owner}/{repo}/info/refs works without .git suffix."""
    resp = await client.get(
        "/testuser/git-test/info/refs?service=git-upload-pack"
    )
    assert resp.status_code == 200
    assert b"# service=git-upload-pack" in resp.content


@pytest.mark.asyncio
async def test_info_refs_nonexistent_repo(client):
    """GET info/refs for non-existent repo returns 404."""
    resp = await client.get(
        "/nobody/nothing.git/info/refs?service=git-upload-pack"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_upload_pack_endpoint(client, test_user, test_token, git_repo):
    """POST /{owner}/{repo}.git/git-upload-pack responds."""
    # Send a minimal (empty) request body — the git process will likely
    # fail or return an error, but we verify the endpoint responds with
    # the correct content type
    resp = await client.post(
        "/testuser/git-test.git/git-upload-pack",
        content=b"0000",
    )
    # The endpoint should respond (even if git process errors)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-git-upload-pack-result"


@pytest.mark.asyncio
async def test_receive_pack_requires_auth(client, test_user, test_token, git_repo):
    """POST git-receive-pack without auth returns 401."""
    resp = await client.post(
        "/testuser/git-test.git/git-receive-pack",
        content=b"0000",
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_receive_pack_with_auth(client, test_user, test_token, git_repo):
    """POST git-receive-pack with auth responds."""
    resp = await client.post(
        "/testuser/git-test.git/git-receive-pack",
        content=b"0000",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-git-receive-pack-result"


@pytest.mark.asyncio
async def test_cache_headers(client, test_user, test_token, git_repo):
    """Git HTTP responses include proper cache-control headers."""
    resp = await client.get(
        "/testuser/git-test.git/info/refs?service=git-upload-pack"
    )
    assert resp.headers.get("cache-control") == "no-cache"
    assert resp.headers.get("pragma") == "no-cache"
