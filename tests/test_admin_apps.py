"""Security and workflow coverage for the admin Apps frontend."""

import hashlib
import re

import pytest
from sqlalchemy import select

from app.admin.routes import _sign_session
from app.models.apps import AppInstallation
from tests.conftest import API, auth_headers


def admin_cookies() -> dict[str, str]:
    return {"admin_session": _sign_session("admin")}


@pytest.mark.asyncio
async def test_apps_and_auth_pages_require_admin_session(client):
    for path in ("/admin/apps", "/admin/auth"):
        response = await client.get(path, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/admin/login"


@pytest.mark.asyncio
async def test_app_create_and_lookup_redact_private_key(client, admin_token):
    response = await client.post(
        "/admin/apps/create",
        cookies=admin_cookies(),
        data={
            "name": "Frontend Test App",
            "slug": "frontend-test-app",
            "app_id": "7001",
            "permission_contents": "write",
        },
    )
    assert response.status_code == 200
    raw_private_key = "-----BEGIN PRIVATE KEY-----"
    assert raw_private_key in response.text
    assert "Copy private key" in response.text

    listing = await client.get("/admin/apps", cookies=admin_cookies())
    assert listing.status_code == 200
    assert "Frontend Test App" in listing.text
    assert raw_private_key not in listing.text

    detail = await client.get("/admin/apps/7001", cookies=admin_cookies())
    assert detail.status_code == 200
    assert "Present (value hidden)" in detail.text
    assert raw_private_key not in detail.text

    api_lookup = await client.get(
        f"{API}/admin/apps/7001",
        headers=auth_headers(admin_token),
    )
    assert api_lookup.status_code == 200
    assert api_lookup.json()["has_private_key"] is True
    assert "private_key" not in api_lookup.json()


@pytest.mark.asyncio
async def test_admin_can_install_app_and_mint_one_time_token(
    client, admin_token, test_repo_with_init, db_session
):
    _owner, _repo, repo = test_repo_with_init
    created = await client.post(
        f"{API}/admin/apps",
        headers=auth_headers(admin_token),
        json={
            "app_id": "7002",
            "name": "Installation Test App",
            "slug": "installation-test-app",
            "permissions": {"contents": "read"},
        },
    )
    assert created.status_code == 201
    private_key = created.json()["private_key"]

    installation_response = await client.post(
        "/admin/apps/7002/installations/create",
        cookies=admin_cookies(),
        data={
            "account_login": "testuser",
            "account_type": "User",
            "repositories": [repo["full_name"]],
        },
    )
    assert installation_response.status_code == 303

    installation = (
        await db_session.execute(select(AppInstallation))
    ).scalar_one()
    installation_id = installation.id
    detail = await client.get(
        f"/admin/installations/{installation_id}", cookies=admin_cookies()
    )
    assert detail.status_code == 200
    assert repo["full_name"] in detail.text
    assert private_key not in detail.text

    token_response = await client.post(
        f"/admin/installations/{installation_id}/tokens/create",
        cookies=admin_cookies(),
        data={"repositories": [repo["full_name"]]},
    )
    assert token_response.status_code == 200
    assert "created-installation-token" in token_response.text, token_response.text
    raw_token = re.search(
        r'<pre[^>]+id="created-installation-token"[^>]*>(.*?)</pre>',
        token_response.text,
        re.DOTALL,
    ).group(1).strip()
    assert raw_token.startswith("ghs_")
    assert "Copy installation token" in token_response.text
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    installation_detail = await client.get(
        f"/admin/installations/{installation_id}", cookies=admin_cookies()
    )
    assert raw_token not in installation_detail.text
    assert token_hash not in installation_detail.text
    assert raw_token[:8] in installation_detail.text

    app_detail = await client.get("/admin/apps/7002", cookies=admin_cookies())
    assert raw_token not in app_detail.text
    assert token_hash not in app_detail.text
    assert "Recent installation-token metadata" in app_detail.text

    auth_page = await client.get("/admin/auth", cookies=admin_cookies())
    assert auth_page.status_code == 200
    assert raw_token not in auth_page.text
    assert token_hash not in auth_page.text
    assert raw_token[:8] in auth_page.text
    assert "Unsupported/deferred" in auth_page.text
