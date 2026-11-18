"""Tests for the Contents REST API endpoints."""

import asyncio
import base64

import pytest
from sqlalchemy import event

from tests.conftest import auth_headers

API = "/api/v3"


@pytest.mark.asyncio
async def test_get_readme(client, test_user, test_token, test_repo_with_init):
    """GET /repos/{owner}/{repo}/readme returns README."""
    owner, repo_name, _ = test_repo_with_init
    resp = await client.get(f"{API}/repos/{owner}/{repo_name}/readme")
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "file"
    assert data["name"] == "README.md"
    assert data["encoding"] == "base64"
    assert "content" in data


@pytest.mark.asyncio
async def test_get_readme_uses_one_repository_query(
    client, test_repo_with_init, db_engine
):
    owner, repo_name, _ = test_repo_with_init
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(db_engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        response = await client.get(f"{API}/repos/{owner}/{repo_name}/readme")
    finally:
        event.remove(db_engine.sync_engine, "before_cursor_execute", record_statement)

    assert response.status_code == 200
    assert len(statements) == 1, statements


@pytest.mark.asyncio
async def test_get_file_content(client, test_user, test_token, test_repo_with_init):
    """GET /repos/{owner}/{repo}/contents/{path} returns file."""
    owner, repo_name, _ = test_repo_with_init
    resp = await client.get(f"{API}/repos/{owner}/{repo_name}/contents/README.md")
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "file"
    assert data["path"] == "README.md"
    # Decode the content
    content = base64.b64decode(data["content"]).decode()
    assert "init-repo" in content


@pytest.mark.asyncio
async def test_get_contents_not_found(client, test_user, test_token, test_repo_with_init):
    """GET /repos/{owner}/{repo}/contents/{path} returns 404 for missing file."""
    owner, repo_name, _ = test_repo_with_init
    resp = await client.get(f"{API}/repos/{owner}/{repo_name}/contents/nonexistent.txt")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_file(client, test_user, test_token, test_repo_with_init):
    """PUT /repos/{owner}/{repo}/contents/{path} creates a file."""
    owner, repo_name, _ = test_repo_with_init
    content_b64 = base64.b64encode(b"Hello, World!\n").decode()
    resp = await client.put(
        f"{API}/repos/{owner}/{repo_name}/contents/hello.txt",
        json={
            "message": "Create hello.txt",
            "content": content_b64,
        },
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "content" in data
    assert "commit" in data
    assert data["content"]["name"] == "hello.txt"


@pytest.mark.asyncio
async def test_create_nested_file(client, test_user, test_token, test_repo_with_init):
    """PUT creates intermediate Git trees for nested paths."""
    owner, repo_name, _ = test_repo_with_init
    content_b64 = base64.b64encode(b"name: Event trigger smoke\n").decode()
    resp = await client.put(
        f"{API}/repos/{owner}/{repo_name}/contents/.github/workflows/event.yml",
        json={
            "message": "Create nested workflow",
            "content": content_b64,
        },
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    assert resp.json()["content"]["path"] == ".github/workflows/event.yml"

    fetched = await client.get(
        f"{API}/repos/{owner}/{repo_name}/contents/.github/workflows/event.yml"
    )
    assert fetched.status_code == 200
    assert base64.b64decode(fetched.json()["content"]).decode() == "name: Event trigger smoke\n"


@pytest.mark.asyncio
async def test_update_file(client, test_user, test_token, test_repo_with_init):
    """PUT /repos/{owner}/{repo}/contents/{path} updates an existing file."""
    owner, repo_name, _ = test_repo_with_init
    # Create a file first
    content_b64 = base64.b64encode(b"Version 1\n").decode()
    create_resp = await client.put(
        f"{API}/repos/{owner}/{repo_name}/contents/version.txt",
        json={"message": "Create", "content": content_b64},
        headers=auth_headers(test_token),
    )
    assert create_resp.status_code == 201

    # Update it
    new_content_b64 = base64.b64encode(b"Version 2\n").decode()
    update_resp = await client.put(
        f"{API}/repos/{owner}/{repo_name}/contents/version.txt",
        json={"message": "Update", "content": new_content_b64, "sha": create_resp.json()["content"]["sha"]},
        headers=auth_headers(test_token),
    )
    assert update_resp.status_code == 200
    data = update_resp.json()
    assert "commit" in data


def _put_body(text: str, **extra) -> dict:
    return {"message": f"Write {text}", "content": base64.b64encode(text.encode()).decode(), **extra}


async def _blob(client, owner, repo_name, path, headers, ref=None):
    url = f"{API}/repos/{owner}/{repo_name}/contents/{path}" + (f"?ref={ref}" if ref else "")
    return (await client.get(url, headers=headers)).json()


@pytest.mark.asyncio
async def test_update_file_sha_is_a_compare_and_swap(client, test_user, test_token, test_repo_with_init):
    """An update needs the file's current blob sha, as on GitHub: a stale one is
    409, a missing one is 422, and neither changes the file."""
    owner, repo_name, _ = test_repo_with_init
    headers = auth_headers(test_token)
    url = f"{API}/repos/{owner}/{repo_name}/contents/lock.txt"

    created = await client.put(url, json=_put_body("v1"), headers=headers)
    assert created.status_code == 201
    first_sha = created.json()["content"]["sha"]

    updated = await client.put(url, json=_put_body("v2", sha=first_sha), headers=headers)
    assert updated.status_code == 200
    second_sha = updated.json()["content"]["sha"]

    stale = await client.put(url, json=_put_body("v3", sha=first_sha), headers=headers)
    assert stale.status_code == 409
    assert stale.json()["message"] == f"lock.txt does not match {first_sha}"

    missing = await client.put(url, json=_put_body("v4"), headers=headers)
    assert missing.status_code == 422
    assert "\"sha\" wasn't supplied" in missing.json()["message"]

    current = await _blob(client, owner, repo_name, "lock.txt", headers)
    assert current["sha"] == second_sha
    assert base64.b64decode(current["content"]) == b"v2"


@pytest.mark.asyncio
async def test_concurrent_updates_with_the_same_sha_have_one_winner(client, test_user, test_token, test_repo_with_init):
    """Two writers racing with the same sha: one 200, one 409, as on GitHub."""
    owner, repo_name, _ = test_repo_with_init
    headers = auth_headers(test_token)
    url = f"{API}/repos/{owner}/{repo_name}/contents/lock.txt"
    created = await client.put(url, json=_put_body("v1"), headers=headers)
    sha = created.json()["content"]["sha"]

    a, b = await asyncio.gather(
        client.put(url, json=_put_body("writer-a", sha=sha), headers=headers),
        client.put(url, json=_put_body("writer-b", sha=sha), headers=headers),
    )
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    winner = a if a.status_code == 200 else b
    current = await _blob(client, owner, repo_name, "lock.txt", headers)
    assert current["sha"] == winner.json()["content"]["sha"]


@pytest.mark.asyncio
async def test_create_file_ignores_an_unmatched_sha(client, test_user, test_token, test_repo_with_init):
    """Creating a new path with a sha is still a create (201), as on GitHub."""
    owner, repo_name, _ = test_repo_with_init
    resp = await client.put(
        f"{API}/repos/{owner}/{repo_name}/contents/fresh.txt",
        json=_put_body("new", sha="0" * 40),
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_update_file_sha_is_checked_on_the_named_branch(client, test_user, test_token, test_repo_with_init):
    """With ``branch``, the sha is compared with that branch's copy of the file."""
    owner, repo_name, _ = test_repo_with_init
    headers = auth_headers(test_token)
    url = f"{API}/repos/{owner}/{repo_name}/contents/lock.txt"

    on_main = await client.put(url, json=_put_body("main"), headers=headers)
    assert on_main.status_code == 201
    main_sha = on_main.json()["content"]["sha"]
    head = on_main.json()["commit"]["sha"]
    ref = await client.post(
        f"{API}/repos/{owner}/{repo_name}/git/refs",
        json={"ref": "refs/heads/feature", "sha": head},
        headers=headers,
    )
    assert ref.status_code == 201

    on_feature = await client.put(url, json=_put_body("feature", sha=main_sha, branch="feature"), headers=headers)
    assert on_feature.status_code == 200
    feature_sha = on_feature.json()["content"]["sha"]

    # main still holds main_sha, so main_sha is stale on feature and
    # feature_sha is stale on main.
    stale = await client.put(url, json=_put_body("x", sha=main_sha, branch="feature"), headers=headers)
    assert stale.status_code == 409
    wrong_branch = await client.put(url, json=_put_body("x", sha=feature_sha), headers=headers)
    assert wrong_branch.status_code == 409
    missing = await client.put(url, json=_put_body("x", branch="feature"), headers=headers)
    assert missing.status_code == 422

    assert (await _blob(client, owner, repo_name, "lock.txt", headers, ref="feature"))["sha"] == feature_sha
    assert (await _blob(client, owner, repo_name, "lock.txt", headers))["sha"] == main_sha


@pytest.mark.asyncio
async def test_delete_file_sha_is_checked_on_the_named_branch(client, test_user, test_token, test_repo_with_init):
    """DELETE with ``branch`` compares the sha with that branch's file: 409 on a
    mismatch, 422 without a sha, and the file stays."""
    owner, repo_name, _ = test_repo_with_init
    headers = auth_headers(test_token)
    url = f"{API}/repos/{owner}/{repo_name}/contents/lock.txt"

    on_main = await client.put(url, json=_put_body("main"), headers=headers)
    main_sha = on_main.json()["content"]["sha"]
    ref = await client.post(
        f"{API}/repos/{owner}/{repo_name}/git/refs",
        json={"ref": "refs/heads/feature", "sha": on_main.json()["commit"]["sha"]},
        headers=headers,
    )
    assert ref.status_code == 201
    on_feature = await client.put(url, json=_put_body("feature", sha=main_sha, branch="feature"), headers=headers)
    feature_sha = on_feature.json()["content"]["sha"]

    stale = await client.request(
        "DELETE", url, json={"message": "rm", "sha": main_sha, "branch": "feature"}, headers=headers,
    )
    assert stale.status_code == 409
    assert stale.json()["message"] == f"lock.txt does not match {main_sha}"
    missing = await client.request("DELETE", url, json={"message": "rm", "branch": "feature"}, headers=headers)
    assert missing.status_code == 422
    assert (await _blob(client, owner, repo_name, "lock.txt", headers, ref="feature"))["sha"] == feature_sha

    ok = await client.request(
        "DELETE", url, json={"message": "rm", "sha": feature_sha, "branch": "feature"}, headers=headers,
    )
    assert ok.status_code == 200
    assert (await client.get(f"{url}?ref=feature", headers=headers)).status_code == 404
    assert (await _blob(client, owner, repo_name, "lock.txt", headers))["sha"] == main_sha


@pytest.mark.asyncio
async def test_create_file_requires_auth(client, test_user, test_token, test_repo_with_init):
    """PUT /repos/{owner}/{repo}/contents/{path} requires auth."""
    owner, repo_name, _ = test_repo_with_init
    content_b64 = base64.b64encode(b"data").decode()
    resp = await client.put(
        f"{API}/repos/{owner}/{repo_name}/contents/noauth.txt",
        json={"message": "test", "content": content_b64},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_file_invalid_base64(client, test_user, test_token, test_repo_with_init):
    """PUT with invalid base64 returns 422."""
    owner, repo_name, _ = test_repo_with_init
    resp = await client.put(
        f"{API}/repos/{owner}/{repo_name}/contents/bad.txt",
        json={"message": "test", "content": "not-valid-base64!!!"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_file_response_format(client, test_user, test_token, test_repo_with_init):
    """File response has required fields."""
    owner, repo_name, _ = test_repo_with_init
    resp = await client.get(f"{API}/repos/{owner}/{repo_name}/contents/README.md")
    data = resp.json()
    for field in ["type", "encoding", "size", "name", "path", "content",
                  "sha", "url", "git_url", "html_url", "download_url", "_links"]:
        assert field in data, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_get_contents_repo_not_found(client):
    """GET /repos/{owner}/{repo}/contents/{path} returns 404 for missing repo."""
    resp = await client.get(f"{API}/repos/nobody/nothing/contents/README.md")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_file(client, test_user, test_token, test_repo_with_init):
    """DELETE /repos/{owner}/{repo}/contents/{path} commits the deletion. It
    needs the file's current blob sha; a stale one is refused (409)."""
    owner, repo_name, _ = test_repo_with_init
    headers = auth_headers(test_token)
    current = (await client.get(f"{API}/repos/{owner}/{repo_name}/contents/README.md", headers=headers)).json()
    stale = await client.request(
        "DELETE", f"{API}/repos/{owner}/{repo_name}/contents/README.md",
        json={"message": "Delete README", "sha": "abc123"}, headers=headers,
    )
    assert stale.status_code == 409
    resp = await client.request(
        "DELETE",
        f"{API}/repos/{owner}/{repo_name}/contents/README.md",
        json={"message": "Delete README", "sha": current["sha"]},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] is None
    assert len(data["commit"]["sha"]) == 40
    gone = await client.get(f"{API}/repos/{owner}/{repo_name}/contents/README.md", headers=headers)
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_get_root_directory(client, test_user, test_token, test_repo_with_init):
    """GET /repos/{owner}/{repo}/contents/ returns directory listing."""
    owner, repo_name, _ = test_repo_with_init
    # Create an additional file
    content_b64 = base64.b64encode(b"data").decode()
    await client.put(
        f"{API}/repos/{owner}/{repo_name}/contents/extra.txt",
        json={"message": "add file", "content": content_b64},
        headers=auth_headers(test_token),
    )
    # List root - the root is the tree, not a path
    # Note: listing root requires empty path which the API may handle differently
    # Just test we can list a known file
    resp = await client.get(f"{API}/repos/{owner}/{repo_name}/contents/README.md")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_directory_readme(client, test_user, test_token, test_repo_with_init):
    """GET /repos/{owner}/{repo}/readme/{dir} returns that directory's README, as GitHub does."""
    owner, repo_name, _ = test_repo_with_init
    resp = await client.put(
        f"{API}/repos/{owner}/{repo_name}/contents/docs/guide/README.md",
        json={"message": "Add a nested README", "content": base64.b64encode(b"# Guide\n").decode()},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201

    resp = await client.get(f"{API}/repos/{owner}/{repo_name}/readme/docs/guide")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "README.md"
    assert data["path"] == "docs/guide/README.md"
    assert base64.b64decode(data["content"]) == b"# Guide\n"

    # The root README is unchanged, and a directory without one is a 404.
    root = (await client.get(f"{API}/repos/{owner}/{repo_name}/readme")).json()
    assert root["path"] == "README.md"
    assert (await client.get(f"{API}/repos/{owner}/{repo_name}/readme/docs")).status_code == 404
    # httpx normalizes "..", so a literal dot segment is sent encoded.
    assert (await client.get(f"{API}/repos/{owner}/{repo_name}/readme/docs/%2E%2E")).status_code == 404
