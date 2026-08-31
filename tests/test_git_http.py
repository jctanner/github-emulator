"""Tests for the Git Smart HTTP protocol endpoints.

These tests verify that the info/refs, upload-pack, and receive-pack
endpoints respond correctly. Full git clone/push integration requires
a running server; these tests validate the HTTP-level behavior.
"""

import gzip
import os

import pytest
from sqlalchemy import select

from app.models.repository import Repository
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
async def test_info_refs_allows_admin_repository_owner(
    client, admin_user, admin_token
):
    """The admin login remains a valid Git owner namespace."""
    created = await client.post(
        f"{API}/user/repos",
        json={"name": "admin-git-test", "auto_init": True},
        headers=auth_headers(admin_token),
    )
    assert created.status_code == 201

    response = await client.get(
        "/admin/admin-git-test.git/info/refs?service=git-upload-pack",
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert (
        response.headers["content-type"]
        == "application/x-git-upload-pack-advertisement"
    )
    assert b"# service=git-upload-pack" in response.content


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
async def test_info_refs_receive_pack_with_many_refs(client, db_session, test_token, git_repo):
    """Authenticated receive-pack discovery handles large ref advertisements."""
    repo = (await db_session.execute(
        select(Repository).where(Repository.full_name == "testuser/git-test")
    )).scalar_one()

    import asyncio

    head_proc = await asyncio.create_subprocess_exec(
        "git", "--git-dir", repo.disk_path, "rev-parse", "refs/heads/main",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await head_proc.communicate()
    head = stdout.decode().strip()
    assert head

    for index in range(600):
        proc = await asyncio.create_subprocess_exec(
            "git", "--git-dir", repo.disk_path, "update-ref",
            f"refs/heads/synthetic/{index:04d}", head,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    resp = await client.get(
        "/testuser/git-test.git/info/refs?service=git-receive-pack",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-git-receive-pack-advertisement"
    assert b"refs/heads/synthetic/0599" in resp.content


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
async def test_upload_pack_spools_request_and_streams_response(
    client, git_repo, monkeypatch, tmp_path,
):
    """Clone/fetch handling should avoid request.body() and stream git output."""
    from app.git import smart_http

    spooled_path = tmp_path / "upload-pack-body"
    called = False

    async def fake_spool_request_body(request, prefix="git-request-"):
        assert prefix == "git-upload-pack-"
        spooled_path.write_bytes(b"0000")
        return str(spooled_path)

    async def fake_stream_git_command_from_file(args, repo_path, input_path):
        nonlocal called
        called = True
        assert args[0] == "git-upload-pack"
        assert input_path == str(spooled_path)
        assert spooled_path.exists()
        yield b"0008ok\n"

    monkeypatch.setattr(smart_http, "_spool_request_body", fake_spool_request_body)
    monkeypatch.setattr(
        smart_http,
        "_stream_git_command_from_file",
        fake_stream_git_command_from_file,
    )

    resp = await client.post(
        "/testuser/git-test.git/git-upload-pack",
        content=b"0000",
    )

    assert resp.status_code == 200
    assert resp.content == b"0008ok\n"
    assert called
    assert not spooled_path.exists()


@pytest.mark.asyncio
async def test_spool_request_body_decodes_gzip():
    """Git clients may gzip Smart HTTP request bodies."""
    from app.git import smart_http

    class Request:
        headers = {"content-encoding": "gzip"}

        async def stream(self):
            body = gzip.compress(b"0000")
            yield body[:3]
            yield body[3:]

    path = await smart_http._spool_request_body(Request(), prefix="git-test-")
    try:
        with open(path, "rb") as f:
            assert f.read() == b"0000"
    finally:
        os.unlink(path)


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
async def test_receive_pack_uses_spooled_body_and_defers_post_push(
    client, test_token, git_repo, monkeypatch, tmp_path,
):
    """Large push handling should avoid request.body() and defer side effects."""
    from app.git import smart_http

    spooled_path = tmp_path / "receive-pack-body"
    post_push_called = False

    async def fake_spool_request_body(request, prefix="git-request-"):
        assert prefix == "git-receive-pack-"
        spooled_path.write_bytes(b"0000")
        return str(spooled_path)

    async def fake_run_git_command_from_file(args, repo_path, input_path):
        assert input_path == str(spooled_path)
        assert spooled_path.exists()
        return 0, b"0008ok\n", b""

    async def fake_post_receive_pack_tasks(repo_id, user_id):
        nonlocal post_push_called
        post_push_called = True

    monkeypatch.setattr(smart_http, "_spool_request_body", fake_spool_request_body)
    monkeypatch.setattr(smart_http, "_run_git_command_from_file", fake_run_git_command_from_file)
    monkeypatch.setattr(smart_http, "_post_receive_pack_tasks", fake_post_receive_pack_tasks)

    resp = await client.post(
        "/testuser/git-test.git/git-receive-pack",
        content=b"x" * (2 * 1024 * 1024),
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    assert resp.content == b"0008ok\n"
    assert not spooled_path.exists()

    import asyncio

    for _ in range(10):
        if post_push_called:
            break
        await asyncio.sleep(0)
    assert post_push_called


@pytest.mark.asyncio
async def test_cache_headers(client, test_user, test_token, git_repo):
    """Git HTTP responses include proper cache-control headers."""
    resp = await client.get(
        "/testuser/git-test.git/info/refs?service=git-upload-pack"
    )
    assert resp.headers.get("cache-control") == "no-cache"
    assert resp.headers.get("pragma") == "no-cache"
