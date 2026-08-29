"""Security and workflow coverage for the admin Apps frontend."""

import hashlib
import re
import time

import pytest
from jose import jwt
from sqlalchemy import select

from app.admin.routes import _sign_session
from app.config import settings
from app.models.apps import AppInstallation, AppInstallationToken, GitHubApp
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
    raw_private_key = "-----BEGIN RSA PRIVATE KEY-----"
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

    reconciled = await client.patch(
        f"{API}/admin/apps/7002",
        headers=auth_headers(admin_token),
        json={
            "name": "Installation Test App",
            "slug": "installation-test-app",
            "permissions": {
                "contents": "read",
                "issues": "write",
                "metadata": "read",
            },
            "sync_installations": True,
        },
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["permissions"] == {
        "contents": "read",
        "issues": "write",
        "metadata": "read",
    }
    assert reconciled.json()["installations_updated"] == 1
    await db_session.refresh(installation)
    assert installation.permissions == reconciled.json()["permissions"]

    duplicate_response = await client.post(
        "/admin/apps/7002/installations/create",
        cookies=admin_cookies(),
        data={
            "account_login": "testuser",
            "account_type": "User",
            "repositories": [repo["full_name"]],
        },
    )
    assert duplicate_response.status_code == 409
    assert f"already has installation #{installation_id}" in duplicate_response.text
    assert repo["full_name"] in duplicate_response.text

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


@pytest.mark.asyncio
async def test_admin_app_lifecycle_controls_rotate_remove_and_delete(
    client, admin_token, test_repo_with_init, db_session
):
    """The admin UI exposes the App lifecycle controls safely."""
    _owner, _repo, repo = test_repo_with_init
    created = await client.post(
        f"{API}/admin/apps",
        headers=auth_headers(admin_token),
        json={
            "app_id": "7003",
            "name": "Lifecycle Test App",
            "slug": "lifecycle-test-app",
            "permissions": {"contents": "read"},
        },
    )
    assert created.status_code == 201
    original_key = created.json()["private_key"]
    client_id = created.json()["client_id"]
    assert client_id.startswith("Iv1.")

    listing = await client.get("/admin/apps", cookies=admin_cookies())
    assert client_id in listing.text
    assert "Delete" in listing.text

    detail = await client.get("/admin/apps/7003", cookies=admin_cookies())
    assert "Client ID" in detail.text
    assert "Regenerate key" in detail.text
    assert "Delete App" in detail.text
    assert original_key not in detail.text

    rotated = await client.post(
        "/admin/apps/7003/regenerate-key",
        cookies=admin_cookies(),
    )
    assert rotated.status_code == 200
    assert "App private key regenerated" in rotated.text
    new_key_match = re.search(
        r'<pre[^>]+id="created-private-key"[^>]*>(.*?)</pre>',
        rotated.text,
        re.DOTALL,
    )
    assert new_key_match is not None
    new_key = new_key_match.group(1).strip()
    assert new_key.startswith("-----BEGIN RSA PRIVATE KEY-----")
    assert new_key != original_key
    assert original_key not in rotated.text

    claims = {"iss": "7003", "iat": int(time.time()) - 10, "exp": int(time.time()) + 60}
    old_jwt = jwt.encode(claims, original_key, algorithm="RS256")
    new_jwt = jwt.encode(claims, new_key, algorithm="RS256")
    previous_permissive = settings.APP_JWT_PERMISSIVE
    settings.APP_JWT_PERMISSIVE = False
    try:
        old_key_response = await client.get(
            f"{API}/app", headers={"Authorization": f"Bearer {old_jwt}"}
        )
        new_key_response = await client.get(
            f"{API}/app", headers={"Authorization": f"Bearer {new_jwt}"}
        )
    finally:
        settings.APP_JWT_PERMISSIVE = previous_permissive
    assert old_key_response.status_code == 401
    assert new_key_response.status_code == 200

    installation_response = await client.post(
        "/admin/apps/7003/installations/create",
        cookies=admin_cookies(),
        data={
            "account_login": "testuser",
            "account_type": "User",
            "repositories": [repo["full_name"]],
        },
    )
    assert installation_response.status_code == 303
    installation = (await db_session.execute(select(AppInstallation))).scalar_one()

    token_response = await client.post(
        f"/admin/installations/{installation.id}/tokens/create",
        cookies=admin_cookies(),
        data={"repositories": [repo["full_name"]]},
    )
    assert token_response.status_code == 200
    token = (await db_session.execute(select(AppInstallationToken))).scalar_one()

    removed = await client.post(
        f"/admin/apps/7003/installations/{installation.id}/delete",
        cookies=admin_cookies(),
        follow_redirects=False,
    )
    assert removed.status_code == 200
    assert f"Removed installation #{installation.id}" in removed.text
    assert "testuser" in removed.text
    assert repo["full_name"] in removed.text
    assert (
        await db_session.execute(
            select(AppInstallation).where(AppInstallation.id == installation.id)
        )
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(
            select(AppInstallationToken).where(AppInstallationToken.id == token.id)
        )
    ).scalar_one_or_none() is None

    deleted = await client.post(
        "/admin/apps/7003/delete",
        cookies=admin_cookies(),
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert (
        await db_session.execute(select(GitHubApp).where(GitHubApp.app_id == "7003"))
    ).scalar_one_or_none() is None
    assert (
        await client.get(
            f"{API}/admin/apps/7003",
            headers=auth_headers(admin_token),
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_installation_token_auth_and_commit_verification(
    client, admin_token, test_repo_with_init
):
    """GitHub App installation tokens authenticate and mark writes verified."""
    _owner, _repo, repo = test_repo_with_init
    created = await client.post(
        "/admin/api/apps",
        headers=auth_headers(admin_token),
        json={"app_id": "7005", "name": "Verification App", "slug": "verification-app"},
    )
    assert created.status_code == 201
    app = created.json()
    claims = {"iss": "7005", "iat": int(time.time()) - 10, "exp": int(time.time()) + 60}
    app_jwt = jwt.encode(claims, app["private_key"], algorithm="RS256")

    installation = await client.post(
        "/admin/api/apps/7005/installations",
        headers=auth_headers(admin_token),
        json={"owner": repo["full_name"].split("/", 1)[0], "repo": repo["name"]},
    )
    assert installation.status_code == 201
    installation_id = installation.json()["id"]
    minted = await client.post(
        f"{API}/app/installations/{installation_id}/access_tokens",
        headers={"Authorization": f"Bearer {app_jwt}"},
        json={"repositories": [repo["full_name"]]},
    )
    assert minted.status_code == 201
    installation_token = minted.json()["token"]
    assert installation_token.startswith("ghs_")

    user_response = await client.get(
        f"{API}/user", headers={"Authorization": f"Bearer {installation_token}"}
    )
    assert user_response.status_code == 200
    assert user_response.json()["login"] == "verification-app[bot]"
    assert user_response.json()["type"] == "Bot"
    commits = await client.get(
        f"{API}/repos/{repo['full_name']}/commits",
        headers={"Authorization": f"Bearer {installation_token}"},
    )
    assert commits.status_code == 200
    assert commits.json()
    assert commits.json()[0]["commit"]["verification"]["verified"] is True
    assert commits.json()[0]["commit"]["verification"]["reason"] == "valid"

    head = commits.json()[0]
    created_commit = await client.post(
        f"{API}/repos/{repo['full_name']}/git/commits",
        headers={"Authorization": f"Bearer {installation_token}"},
        json={
            "message": "verified installation commit",
            "tree": head["commit"]["tree"]["sha"],
            "parents": [head["sha"]],
        },
    )
    assert created_commit.status_code == 201
    assert created_commit.json()["verification"]["verified"] is True


@pytest.mark.asyncio
async def test_app_jwt_permissive_mode_can_be_disabled(client, admin_token):
    """The compatibility default accepts a wrong signature, strict mode rejects it."""
    created = await client.post(
        "/admin/api/apps",
        headers=auth_headers(admin_token),
        json={"app_id": "7006", "name": "JWT Mode App", "slug": "jwt-mode-app"},
    )
    assert created.status_code == 201
    claims = {"iss": "7006", "iat": int(time.time()) - 10, "exp": int(time.time()) + 60}
    # A second generated RSA key gives a valid JWT with the wrong signature.
    from app.api.apps import _private_key

    wrong_jwt = jwt.encode(claims, _private_key(), algorithm="RS256")
    previous_permissive = settings.APP_JWT_PERMISSIVE
    try:
        settings.APP_JWT_PERMISSIVE = True
        permissive = await client.get(
            f"{API}/app", headers={"Authorization": f"Bearer {wrong_jwt}"}
        )
        settings.APP_JWT_PERMISSIVE = False
        strict = await client.get(
            f"{API}/app", headers={"Authorization": f"Bearer {wrong_jwt}"}
        )
    finally:
        settings.APP_JWT_PERMISSIVE = previous_permissive
    assert permissive.status_code == 200
    assert strict.status_code == 401


@pytest.mark.asyncio
async def test_pr_style_admin_api_and_richer_app_payloads(
    client, admin_token, test_repo_with_init, db_session
):
    """The PR-style JSON paths remain available over the current App model."""
    _owner, _repo, repo = test_repo_with_init
    created = await client.post(
        "/admin/api/apps",
        headers=auth_headers(admin_token),
        json={
            "app_id": "7004",
            "name": "Compatibility App",
            "slug": "compatibility-app",
            "permissions": {"issues": "write"},
        },
    )
    assert created.status_code == 201
    app = created.json()
    assert app["app_id"] == "7004"
    assert app["client_id"].startswith("Iv1.")
    private_key = app["private_key"]

    persisted = (
        await db_session.execute(select(GitHubApp).where(GitHubApp.app_id == "7004"))
    ).scalar_one()
    assert persisted.client_id == app["client_id"]

    listing = await client.get("/admin/api/apps", headers=auth_headers(admin_token))
    assert listing.status_code == 200
    assert listing.json()[0]["client_id"] == app["client_id"]

    detail = await client.get(
        "/admin/api/apps/7004", headers=auth_headers(admin_token)
    )
    assert detail.status_code == 200
    assert detail.json()["has_private_key"] is True
    assert "private_key" not in detail.json()

    downloaded = await client.get(
        "/admin/api/apps/7004/private-key", headers=auth_headers(admin_token)
    )
    assert downloaded.status_code == 200
    assert downloaded.json()["private_key"] == private_key

    rotated = await client.post(
        "/admin/api/apps/7004/private-key/regenerate",
        headers=auth_headers(admin_token),
    )
    assert rotated.status_code == 200
    assert rotated.json()["private_key"] != private_key

    installation = await client.post(
        "/admin/api/apps/7004/installations",
        headers=auth_headers(admin_token),
        json={"owner": "testuser", "repo": repo["name"]},
    )
    assert installation.status_code == 201
    installation_data = installation.json()
    assert installation_data["owner"] == "testuser"
    assert installation_data["repo"] == repo["name"]

    app_jwt = jwt.encode(
        {"iss": "7004", "iat": int(time.time()) - 10, "exp": int(time.time()) + 60},
        rotated.json()["private_key"],
        algorithm="RS256",
    )
    app_headers = {"Authorization": f"Bearer {app_jwt}"}
    app_payload = await client.get(f"{API}/app", headers=app_headers)
    assert app_payload.status_code == 200
    assert app_payload.json()["client_id"] == app["client_id"]
    assert app_payload.json()["node_id"] == "A_7004"
    assert app_payload.json()["installations_count"] == 1
    assert app_payload.json()["external_url"].endswith("/apps/compatibility-app")

    installations = await client.get(
        f"{API}/app/installations", headers=app_headers
    )
    assert installations.status_code == 200
    installation_payload = installations.json()[0]
    assert installation_payload["app_slug"] == "compatibility-app"
    assert installation_payload["repository_selection"] == "selected"
    assert installation_payload["access_tokens_url"].endswith(
        f"/app/installations/{installation_data['id']}/access_tokens"
    )
