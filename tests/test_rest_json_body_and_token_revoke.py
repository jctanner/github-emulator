"""Two REST behaviours clients depend on.

GitHub parses a REST body as JSON whatever its Content-Type, so
``curl -d '{"body": ...}'`` (form-encoded by default) creates a comment.
``DELETE /installation/token`` revokes the calling installation token with
204, and the token is refused afterwards.
"""

import json
import time

import pytest
from jose import jwt

from tests.conftest import API, auth_headers

pytestmark = pytest.mark.asyncio


async def _issue(client, owner, repo, token):
    resp = await client.post(
        f"{API}/repos/{owner}/{repo}/issues",
        json={"title": "t"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["number"]


@pytest.mark.parametrize(
    "content_type",
    [None, "application/x-www-form-urlencoded", "text/plain; charset=utf-8"],
)
async def test_a_json_body_is_read_whatever_the_content_type(
    client, test_repo_with_init, test_token, content_type
):
    owner, repo, _ = test_repo_with_init
    number = await _issue(client, owner, repo, test_token)
    headers = auth_headers(test_token)
    if content_type:
        headers["Content-Type"] = content_type
    resp = await client.post(
        f"{API}/repos/{owner}/{repo}/issues/{number}/comments",
        content=json.dumps({"body": "hello"}),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["body"] == "hello"


async def test_a_body_that_is_not_json_is_still_refused(
    client, test_repo_with_init, test_token
):
    owner, repo, _ = test_repo_with_init
    number = await _issue(client, owner, repo, test_token)
    headers = auth_headers(test_token)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    resp = await client.post(
        f"{API}/repos/{owner}/{repo}/issues/{number}/comments",
        content="body=hello",
        headers=headers,
    )
    assert resp.status_code == 422


async def test_an_asset_upload_keeps_its_content_type(
    client, test_repo_with_init, test_token
):
    owner, repo, _ = test_repo_with_init
    release = await client.post(
        f"{API}/repos/{owner}/{repo}/releases",
        json={"tag_name": "v1"},
        headers=auth_headers(test_token),
    )
    assert release.status_code == 201, release.text
    headers = auth_headers(test_token)
    headers["Content-Type"] = "text/plain"
    resp = await client.post(
        f"{API}/repos/{owner}/{repo}/releases/{release.json()['id']}/assets?name=a.json",
        content=b'{"a": 1}',
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["content_type"] == "text/plain"


async def _installation_token(client, admin_token, repositories):
    created = await client.post(
        f"{API}/admin/apps", headers=auth_headers(admin_token),
        json={"app_id": "5101", "name": "revoker", "slug": "revoker", "permissions": {"issues": "write", "metadata": "read"}},
    )
    assert created.status_code == 201, created.text
    installation = await client.post(
        f"{API}/admin/apps/5101/installations", headers=auth_headers(admin_token),
        json={"account_login": "testuser", "account_type": "User", "repositories": repositories, "permissions": {"issues": "write", "metadata": "read"}},
    )
    assert installation.status_code == 201, installation.text
    now = int(time.time())
    app_jwt = jwt.encode({"iss": "5101", "iat": now - 10, "exp": now + 60}, created.json()["private_key"], algorithm="RS256")
    minted = await client.post(
        f"{API}/app/installations/{installation.json()['id']}/access_tokens",
        headers={"Authorization": f"Bearer {app_jwt}"},
        json={},
    )
    assert minted.status_code == 201, minted.text
    return minted.json()["token"]


async def test_an_installation_token_revokes_itself(
    client, admin_token, test_repo_with_init
):
    owner, repo, _ = test_repo_with_init
    token = await _installation_token(client, admin_token, [f"{owner}/{repo}"])
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get(f"{API}/installation/repositories", headers=headers)).status_code == 200

    resp = await client.delete(f"{API}/installation/token", headers=headers)
    assert resp.status_code == 204, resp.text
    assert resp.content == b""

    # The revoked token no longer authenticates.
    assert (await client.get(f"{API}/installation/repositories", headers=headers)).status_code == 403
    assert (await client.get(f"{API}/user", headers=headers)).status_code == 401


async def test_a_personal_token_cannot_use_the_revoke_route(client, test_token):
    resp = await client.delete(f"{API}/installation/token", headers=auth_headers(test_token))
    assert resp.status_code == 403
