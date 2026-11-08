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

    # As on GitHub, an installation token is not a person: /user refuses
    # it, and the bot's login comes from GraphQL's viewer.
    whoami = await client.get(f"{API}/user", headers={"Authorization": f"Bearer {token}"})
    assert whoami.status_code == 403
    assert "Resource not accessible by integration" in whoami.json()["message"]
    assert (await client.get(f"{API}/user/repos", headers={"Authorization": f"Bearer {token}"})).status_code == 403
    viewer = await client.post("/api/graphql", headers={"Authorization": f"Bearer {token}"},
                               json={"query": "{ viewer { login } }"})
    assert viewer.status_code == 200, viewer.text
    assert viewer.json()["data"]["viewer"]["login"] == "fullsend-triage[bot]"
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
    # Issuing a token needs a real job to issue it for; that path lives in
    # tests/actions/test_oidc_claims.py. What this asserts is that an
    # unauthenticated caller gets nothing.
    token = await client.get("/actions/oidc/token?audience=fullsend-mint")
    assert token.status_code == 401


@pytest.mark.asyncio
async def test_a_site_admin_issues_a_longer_lived_installation_token(client, admin_token, test_token, test_repo_with_init):
    """The admin route issues a bot token without the App's JWT, for longer
    than GitHub's hour but at most a day; nobody else may use it."""
    _owner, _repo, repo = test_repo_with_init
    created = await client.post(
        f"{API}/admin/apps", headers=auth_headers(admin_token),
        json={"app_id": "1009", "name": "Fullsend E2E", "slug": "fullsend-e2e", "permissions": {"issues": "write"}},
    )
    assert created.status_code == 201
    installation = await client.post(
        f"{API}/admin/apps/1009/installations", headers=auth_headers(admin_token),
        json={"account_login": "testuser", "account_type": "User", "repositories": [repo["full_name"]]},
    )
    path = f"{API}/admin/apps/1009/installations/{installation.json()['id']}/access_tokens"

    assert (await client.post(path, headers=auth_headers(test_token), json={})).status_code == 403
    for bad in (0, -5, "3600", True, 86401):
        assert (await client.post(path, headers=auth_headers(admin_token), json={"expires_in": bad})).status_code == 422
    assert (await client.post(f"{API}/admin/apps/1009/installations/999999/access_tokens",
                              headers=auth_headers(admin_token), json={})).status_code == 404

    issued = await client.post(path, headers=auth_headers(admin_token), json={"expires_in": 6 * 3600})
    assert issued.status_code == 201
    body = issued.json()
    from datetime import datetime, timezone
    lifetime = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")) - datetime.now(timezone.utc)
    assert 5.9 * 3600 < lifetime.total_seconds() <= 6 * 3600
    assert body["permissions"] == {"issues": "write"}
    viewer = await client.post("/api/graphql", headers={"Authorization": f"token {body['token']}"},
                               json={"query": "{ viewer { login } }"})
    assert viewer.json()["data"]["viewer"]["login"] == "fullsend-e2e[bot]"
    # Never wider than the installation.
    wider = await client.post(path, headers=auth_headers(admin_token), json={"permissions": {"contents": "write"}})
    assert wider.status_code == 422
