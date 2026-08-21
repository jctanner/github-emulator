"""Resettable GitHub App, installation, and Actions OIDC coverage."""

import time

import pytest
from jose import jwt

from tests.conftest import API, auth_headers


@pytest.mark.asyncio
async def test_app_installation_token_is_repo_scoped(client, admin_token, test_repo_with_init):
    _owner, _repo, repo = test_repo_with_init
    created = await client.post(
        f"{API}/admin/apps",
        headers=auth_headers(admin_token),
        json={"app_id": "1001", "name": "Fullsend Triage", "slug": "fullsend-triage", "permissions": {"issues": "write"}},
    )
    assert created.status_code == 201
    app = created.json()
    now = int(time.time())
    app_jwt = jwt.encode({"iss": "1001", "iat": now - 10, "exp": now + 60}, app["private_key"], algorithm="RS256")
    headers = {"Authorization": f"Bearer {app_jwt}"}

    assert (await client.get(f"{API}/app", headers=headers)).json()["slug"] == "fullsend-triage"
    installation = await client.post(
        f"{API}/admin/apps/1001/installations",
        headers=auth_headers(admin_token),
        json={"account_login": "testuser", "account_type": "User", "repositories": [repo["full_name"]]},
    )
    assert installation.status_code == 201
    installation_id = installation.json()["id"]
    token_response = await client.post(f"{API}/app/installations/{installation_id}/access_tokens", headers=headers, json={})
    assert token_response.status_code == 201
    token = token_response.json()["token"]

    whoami = await client.get(f"{API}/user", headers={"Authorization": f"Bearer {token}"})
    assert whoami.status_code == 200
    assert whoami.json()["login"] == "testuser"
    repositories = await client.get(f"{API}/app/installations/{installation_id}/repositories", headers=headers)
    assert repositories.status_code == 200
    assert repositories.json()["total_count"] == 1


@pytest.mark.asyncio
async def test_actions_oidc_issuer_exposes_jwks(client):
    configuration = await client.get("/.well-known/openid-configuration")
    assert configuration.status_code == 200
    assert configuration.json()["issuer"] == "http://testserver"
    keys = await client.get("/.well-known/jwks.json")
    assert keys.status_code == 200
    assert keys.json()["keys"][0]["alg"] == "RS256"
    token = await client.get(
        "/actions/oidc/token?audience=fullsend-mint",
        headers={"Authorization": "Bearer fullsend-action-request"},
    )
    assert token.status_code == 200
    claims = jwt.get_unverified_claims(token.json()["value"])
    assert claims["iss"] == "http://testserver"
    assert claims["aud"] == "fullsend-mint"
