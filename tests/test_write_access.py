"""Writes to contents, refs, and git objects need write access.

These endpoints took any authenticated user: a bot whose installation
granted only contents: read could still write files and move refs, so a
role's permission level meant nothing past the git transport. Now the same
rule applies everywhere the transport applies it: owner, site admin, a
collaborator with push or better, or an installed App's bot whose
installation carries contents: write.
"""

import base64
import time

import pytest
from jose import jwt

from tests.conftest import API, auth_headers

pytestmark = pytest.mark.asyncio


async def _installation_token(client, admin_token, app_id, slug, permissions):
    created = await client.post(
        f"{API}/admin/apps", headers=auth_headers(admin_token),
        json={"app_id": app_id, "name": slug, "slug": slug, "permissions": permissions},
    )
    assert created.status_code == 201, created.text
    app = created.json()
    installation = await client.post(
        f"{API}/admin/apps/{app_id}/installations", headers=auth_headers(admin_token),
        json={"account_login": "testuser", "account_type": "User", "repositories": [], "permissions": permissions},
    )
    assert installation.status_code == 201, installation.text
    now = int(time.time())
    app_jwt = jwt.encode({"iss": app_id, "iat": now - 10, "exp": now + 60}, app["private_key"], algorithm="RS256")
    minted = await client.post(
        f"{API}/app/installations/{installation.json()['id']}/access_tokens",
        headers={"Authorization": f"Bearer {app_jwt}"}, json={"repositories": ["init-repo"]},
    )
    assert minted.status_code == 201, minted.text
    return {"Authorization": f"Bearer {minted.json()['token']}"}


def _file(name="hello.txt"):
    return {"message": "add", "content": base64.b64encode(b"hi").decode()}


async def test_a_read_only_installation_cannot_write_contents_or_refs(client, admin_token, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    headers = await _installation_token(client, admin_token, "3001", "reader", {"contents": "read"})
    put = await client.put(f"{API}/repos/{owner}/{repo}/contents/hello.txt", headers=headers, json=_file())
    assert put.status_code == 403, put.text
    sha = (await client.get(f"{API}/repos/{owner}/{repo}/commits/main", headers=auth_headers(test_token))).json()["sha"]
    ref = await client.post(f"{API}/repos/{owner}/{repo}/git/refs", headers=headers, json={"ref": "refs/heads/x", "sha": sha})
    assert ref.status_code == 403
    blob = await client.post(f"{API}/repos/{owner}/{repo}/git/blobs", headers=headers, json={"content": "x", "encoding": "utf-8"})
    assert blob.status_code == 403
    # Reading is still fine.
    assert (await client.get(f"{API}/repos/{owner}/{repo}/contents/README.md", headers=headers)).status_code == 200


async def test_a_write_installation_may_write(client, admin_token, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    headers = await _installation_token(client, admin_token, "3002", "writer", {"contents": "write"})
    put = await client.put(f"{API}/repos/{owner}/{repo}/contents/hello.txt", headers=headers, json=_file())
    assert put.status_code in (200, 201), put.text
    sha = (await client.get(f"{API}/repos/{owner}/{repo}/commits/main", headers=auth_headers(test_token))).json()["sha"]
    ref = await client.post(f"{API}/repos/{owner}/{repo}/git/refs", headers=headers, json={"ref": "refs/heads/x", "sha": sha})
    assert ref.status_code == 201, ref.text


async def test_a_pull_collaborator_cannot_write_but_a_push_one_can(client, admin_token, admin_user, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    grant = await client.put(f"{API}/repos/{owner}/{repo}/collaborators/{admin_user.login}",
                             headers=auth_headers(test_token), json={"permission": "pull"})
    assert grant.status_code in (201, 204), grant.text
    # A site admin bypasses everything, so demote the check to a plain user by
    # asserting the collaborator path through a second, non-admin account.
    other = await client.post(f"{API}/admin/users", headers=auth_headers(admin_token),
                              json={"login": "puller", "email": "puller@example.com", "password": "x"})
    if other.status_code not in (200, 201):
        pytest.skip("no admin user creation endpoint in this build")
    token = await client.post(f"{API}/admin/tokens", headers=auth_headers(admin_token),
                              json={"login": "puller", "name": "t", "scopes": ["repo"]})
    assert token.status_code in (200, 201), token.text
    puller = auth_headers(token.json()["token"])
    await client.put(f"{API}/repos/{owner}/{repo}/collaborators/puller", headers=auth_headers(test_token), json={"permission": "pull"})
    assert (await client.put(f"{API}/repos/{owner}/{repo}/contents/a.txt", headers=puller, json=_file())).status_code == 403
    await client.put(f"{API}/repos/{owner}/{repo}/collaborators/puller", headers=auth_headers(test_token), json={"permission": "push"})
    assert (await client.put(f"{API}/repos/{owner}/{repo}/contents/a.txt", headers=puller, json=_file())).status_code in (200, 201)
