"""An installation token carries exactly what it was minted with.

Fullsend's mint downscopes a role's permissions to the requested level and
scopes the token to one repository. The emulator used to consult only the
App's installation when it decided a write, so a token minted at the read
level from a write-capable installation could still write, and a token
minted for one repository could act on another the installation covered.
GitHub answers for both at the gateway: a repository outside the token's
selection is Not Found, and an endpoint the token's permissions do not
cover is "Resource not accessible by integration". So does the emulator now,
for every route including the git transport, and the mint refuses to widen
a token beyond its installation.
"""

import base64
import time

import pytest
from jose import jwt

from tests.conftest import API, auth_headers

pytestmark = pytest.mark.asyncio


async def _app(client, admin_token, app_id, slug, permissions, repositories):
    created = await client.post(
        f"{API}/admin/apps", headers=auth_headers(admin_token),
        json={"app_id": app_id, "name": slug, "slug": slug, "permissions": permissions},
    )
    assert created.status_code == 201, created.text
    installation = await client.post(
        f"{API}/admin/apps/{app_id}/installations", headers=auth_headers(admin_token),
        json={"account_login": "testuser", "account_type": "User", "repositories": repositories, "permissions": permissions},
    )
    assert installation.status_code == 201, installation.text
    now = int(time.time())
    app_jwt = jwt.encode({"iss": app_id, "iat": now - 10, "exp": now + 60}, created.json()["private_key"], algorithm="RS256")
    return installation.json()["id"], {"Authorization": f"Bearer {app_jwt}"}


async def _mint(client, installation_id, app_headers, repositories, permissions=None):
    body = {"repositories": repositories}
    if permissions is not None:
        body["permissions"] = permissions
    resp = await client.post(f"{API}/app/installations/{installation_id}/access_tokens", headers=app_headers, json=body)
    return resp


def _bearer(resp):
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def _file():
    return {"message": "add", "content": base64.b64encode(b"hi").decode()}


async def test_a_read_level_token_cannot_write_what_its_installation_could(client, admin_token, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    installation_id, app = await _app(client, admin_token, "4001", "coder", {"contents": "write", "issues": "write", "metadata": "read"}, [])
    read = await _mint(client, installation_id, app, [repo], {"contents": "read", "issues": "read", "metadata": "read"})
    assert read.status_code == 201, read.text
    assert read.json()["permissions"]["contents"] == "read"
    headers = _bearer(read)
    put = await client.put(f"{API}/repos/{owner}/{repo}/contents/x.txt", headers=headers, json=_file())
    assert put.status_code == 403
    assert "Resource not accessible by integration" in put.text
    comment = await client.post(f"{API}/repos/{owner}/{repo}/issues", headers=headers, json={"title": "t"})
    assert comment.status_code == 403
    # Reads within the grant still work.
    assert (await client.get(f"{API}/repos/{owner}/{repo}/contents/README.md", headers=headers)).status_code == 200
    assert (await client.get(f"{API}/repos/{owner}/{repo}/issues", headers=headers)).status_code == 200

    write = await _mint(client, installation_id, app, [repo], {"contents": "write", "issues": "write", "metadata": "read"})
    headers = _bearer(write)
    assert (await client.put(f"{API}/repos/{owner}/{repo}/contents/x.txt", headers=headers, json=_file())).status_code in (200, 201)
    assert (await client.post(f"{API}/repos/{owner}/{repo}/issues", headers=headers, json={"title": "t"})).status_code == 201


async def test_a_token_without_pull_requests_cannot_read_or_write_them(client, admin_token, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    installation_id, app = await _app(client, admin_token, "4002", "triager", {"contents": "read", "issues": "write", "metadata": "read"}, [])
    headers = _bearer(await _mint(client, installation_id, app, [repo]))
    assert (await client.get(f"{API}/repos/{owner}/{repo}/pulls", headers=headers)).status_code == 403
    # Metadata and the repository itself stay readable, as they do on GitHub.
    assert (await client.get(f"{API}/repos/{owner}/{repo}", headers=headers)).status_code == 200


async def test_a_token_is_bound_to_the_repositories_it_was_minted_for(client, admin_token, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    other = await client.post(f"{API}/user/repos", json={"name": "other", "auto_init": True}, headers=auth_headers(test_token))
    assert other.status_code == 201
    installation_id, app = await _app(client, admin_token, "4003", "scoped", {"contents": "write", "metadata": "read"}, [])
    headers = _bearer(await _mint(client, installation_id, app, [repo]))
    assert (await client.get(f"{API}/repos/{owner}/{repo}", headers=headers)).status_code == 200
    assert (await client.get(f"{API}/repos/{owner}/other", headers=headers)).status_code == 404
    assert (await client.put(f"{API}/repos/{owner}/other/contents/x.txt", headers=headers, json=_file())).status_code == 404


async def test_the_mint_refuses_to_widen_a_token_beyond_its_installation(client, admin_token, test_repo_with_init):
    owner, repo, _ = test_repo_with_init
    installation_id, app = await _app(client, admin_token, "4004", "narrow", {"contents": "read", "metadata": "read"}, [])
    widened = await _mint(client, installation_id, app, [repo], {"contents": "write"})
    assert widened.status_code == 422
    assert "exceeds the installation" in widened.text
    foreign = await _mint(client, installation_id, app, [repo], {"issues": "read"})
    assert foreign.status_code == 422
    same = await _mint(client, installation_id, app, [repo], {"contents": "read"})
    assert same.status_code == 201


async def test_the_git_transport_honours_the_tokens_contents_permission(client, admin_token, test_repo_with_init):
    owner, repo, _ = test_repo_with_init
    installation_id, app = await _app(client, admin_token, "4005", "pusher", {"contents": "write", "metadata": "read"}, [])
    read = _bearer(await _mint(client, installation_id, app, [repo], {"contents": "read"}))
    fetch = await client.get(f"/{owner}/{repo}.git/info/refs?service=git-upload-pack", headers=read)
    assert fetch.status_code == 200
    push = await client.get(f"/{owner}/{repo}.git/info/refs?service=git-receive-pack", headers=read)
    assert push.status_code == 403
    write = _bearer(await _mint(client, installation_id, app, [repo], {"contents": "write"}))
    assert (await client.get(f"/{owner}/{repo}.git/info/refs?service=git-receive-pack", headers=write)).status_code == 200


async def test_variables_and_secrets_are_their_own_app_permissions(client, admin_token, test_repo_with_init):
    """An onboarding App carries actions_variables: write and secrets: write,
    not actions; setting a repository variable was refused for want of
    actions (2026-09-29, experiment/testrepo)."""
    owner, repo, _ = test_repo_with_init
    installation_id, app = await _app(
        client, admin_token, "4006", "onboarder",
        {"contents": "write", "actions_variables": "write", "secrets": "write", "metadata": "read"}, [],
    )
    headers = _bearer(await _mint(client, installation_id, app, [repo]))
    variable = await client.post(
        f"{API}/repos/{owner}/{repo}/actions/variables", headers=headers,
        json={"name": "FULLSEND_GCP_REGION", "value": "global"},
    )
    assert variable.status_code in (201, 204), variable.text
    public_key = await client.get(f"{API}/repos/{owner}/{repo}/actions/secrets/public-key", headers=headers)
    assert public_key.status_code == 200, public_key.text
    # No actions permission: a workflow dispatch is still refused.
    dispatch = await client.post(
        f"{API}/repos/{owner}/{repo}/actions/workflows/x.yml/dispatches", headers=headers, json={"ref": "main"},
    )
    assert dispatch.status_code == 403

    read_only_id, read_only_app = await _app(
        client, admin_token, "4007", "reader2", {"actions_variables": "read", "metadata": "read"}, [],
    )
    reader = _bearer(await _mint(client, read_only_id, read_only_app, [repo]))
    assert (await client.get(f"{API}/repos/{owner}/{repo}/actions/variables", headers=reader)).status_code == 200
    denied = await client.post(
        f"{API}/repos/{owner}/{repo}/actions/variables", headers=reader,
        json={"name": "X", "value": "y"},
    )
    assert denied.status_code == 403 and "actions_variables" in denied.text
