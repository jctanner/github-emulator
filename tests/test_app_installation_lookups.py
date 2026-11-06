"""GitHub App and installation lookups that token brokers depend on.

A service that mints installation tokens first finds the installation: by
repository or organization with the App's JWT, by listing an organization's
installations as its owner, and by resolving an App from its slug. Fullsend's
mint and ``fullsend github setup`` use all four (github-emulator#19). The
emulator lacked three of them, matched a repository only when an installation
listed it by name (so a "all repositories" installation covered nothing), and
ignored which App signed the JWT. It also returned App ids as JSON strings,
which clients decoding ``id`` into an integer reject.
"""

import hashlib
import secrets
import time

import pytest
from jose import jwt

from app.models.token import PersonalAccessToken
from app.models.user import User
from tests.conftest import API, auth_headers

pytestmark = pytest.mark.asyncio

ORG = "lookup-org"


async def _org_with_repo(client, test_token):
    headers = auth_headers(test_token)
    created = await client.post(f"{API}/orgs", json={"login": ORG}, headers=headers)
    assert created.status_code == 201, created.text
    repo = await client.post(f"{API}/orgs/{ORG}/repos", json={"name": "target"}, headers=headers)
    assert repo.status_code == 201, repo.text
    return f"{ORG}/target"


async def _app(client, admin_token, app_id, slug, repositories, account=ORG):
    """Create an App, install it on ``account``, and return (installation, JWT headers)."""
    created = await client.post(
        f"{API}/admin/apps", headers=auth_headers(admin_token),
        json={"app_id": app_id, "name": slug, "slug": slug, "permissions": {"issues": "write"}},
    )
    assert created.status_code == 201, created.text
    installation = await client.post(
        f"{API}/admin/apps/{app_id}/installations", headers=auth_headers(admin_token),
        json={"account_login": account, "account_type": "Organization", "repositories": repositories},
    )
    assert installation.status_code == 201, installation.text
    now = int(time.time())
    app_jwt = jwt.encode(
        {"iss": app_id, "iat": now - 10, "exp": now + 60},
        created.json()["private_key"], algorithm="RS256",
    )
    return installation.json(), {"Authorization": f"Bearer {app_jwt}"}


async def test_app_ids_are_integers(client, admin_token, test_token):
    full_name = await _org_with_repo(client, test_token)
    installation, headers = await _app(client, admin_token, "2001", "int-app", [full_name])

    app = (await client.get(f"{API}/app", headers=headers)).json()
    assert app["id"] == 2001
    assert installation["app_id"] == 2001
    listed = (await client.get(f"{API}/app/installations", headers=headers)).json()
    assert listed[0]["app_id"] == 2001
    # The response model of this route was typed str and would reject an int.
    by_repo = await client.get(f"{API}/repos/{full_name}/installations", headers=auth_headers(admin_token))
    assert by_repo.status_code == 200, by_repo.text
    assert by_repo.json()[0]["app_id"] == 2001


async def test_non_numeric_app_id_is_rejected(client, admin_token):
    for path in ("/admin/apps", "/admin/api/apps"):
        url = f"{API}{path}" if path == "/admin/apps" else path
        response = await client.post(
            url, headers=auth_headers(admin_token),
            json={"app_id": "not-a-number", "name": "Bad", "slug": f"bad{len(path)}"},
        )
        assert response.status_code == 422, (path, response.text)


async def test_get_app_by_slug(client, admin_token, test_token):
    full_name = await _org_with_repo(client, test_token)
    await _app(client, admin_token, "2002", "slug-app", [full_name])

    response = await client.get(f"{API}/apps/slug-app", headers=auth_headers(test_token))
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 2002
    assert body["slug"] == "slug-app"
    assert body["client_id"].startswith("Iv1.")
    assert "private_key" not in body
    assert (await client.get(f"{API}/apps/no-such-app")).status_code == 404


async def test_repo_installation_covers_all_repositories(client, admin_token, test_token):
    full_name = await _org_with_repo(client, test_token)
    installation, headers = await _app(client, admin_token, "2003", "all-app", [])

    response = await client.get(f"{API}/repos/{full_name}/installation", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == installation["id"]
    assert body["app_id"] == 2003
    assert body["repository_selection"] == "all"
    assert body["permissions"] == {"issues": "write"}

    listed = await client.get(f"{API}/repos/{full_name}/installations", headers=auth_headers(admin_token))
    assert [item["id"] for item in listed.json()] == [installation["id"]]


async def test_repo_installation_is_the_calling_apps(client, admin_token, test_token):
    full_name = await _org_with_repo(client, test_token)
    first, first_headers = await _app(client, admin_token, "2004", "first-app", [full_name])
    second, second_headers = await _app(client, admin_token, "2005", "second-app", [full_name])
    _other, other_headers = await _app(client, admin_token, "2006", "other-app", ["lookup-org/elsewhere"])

    got_first = await client.get(f"{API}/repos/{full_name}/installation", headers=first_headers)
    got_second = await client.get(f"{API}/repos/{full_name}/installation", headers=second_headers)
    assert got_first.json()["id"] == first["id"]
    assert got_second.json()["id"] == second["id"]
    # Installed on the organization, but not on this repository.
    assert (await client.get(f"{API}/repos/{full_name}/installation", headers=other_headers)).status_code == 404


async def test_org_installation_requires_the_apps_jwt(client, admin_token, test_token):
    await _org_with_repo(client, test_token)
    installation, headers = await _app(client, admin_token, "2007", "org-app", [])
    _elsewhere, elsewhere_headers = await _app(
        client, admin_token, "2008", "user-app", [], account="testuser"
    )

    response = await client.get(f"{API}/orgs/{ORG}/installation", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == installation["id"]
    assert response.json()["app_slug"] == "org-app"
    assert (await client.get(f"{API}/orgs/{ORG}/installation", headers=elsewhere_headers)).status_code == 404
    assert (await client.get(f"{API}/orgs/{ORG}/installation", headers=auth_headers(admin_token))).status_code == 401


async def test_list_org_installations(client, admin_token, test_token, db_session):
    full_name = await _org_with_repo(client, test_token)
    first, _ = await _app(client, admin_token, "2009", "list-one", [full_name])
    second, _ = await _app(client, admin_token, "2010", "list-two", [])
    await _app(client, admin_token, "2011", "not-this-org", [], account="testuser")

    # The organization's creator is its admin.
    response = await client.get(f"{API}/orgs/{ORG}/installations", headers=auth_headers(test_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_count"] == 2
    assert [(item["id"], item["app_id"], item["app_slug"]) for item in body["installations"]] == [
        (first["id"], 2009, "list-one"),
        (second["id"], 2010, "list-two"),
    ]
    assert body["installations"][0]["permissions"] == {"issues": "write"}

    paged = await client.get(
        f"{API}/orgs/{ORG}/installations?per_page=1&page=2", headers=auth_headers(test_token)
    )
    assert paged.json()["total_count"] == 2
    assert [item["id"] for item in paged.json()["installations"]] == [second["id"]]
    assert 'rel="prev"' in paged.headers["Link"]

    outsider = User(login="outsider", email="outsider@example.com", hashed_password="x", site_admin=False)
    db_session.add(outsider)
    await db_session.commit()
    await db_session.refresh(outsider)
    raw = f"ghp_{secrets.token_hex(20)}"
    db_session.add(PersonalAccessToken(
        user_id=outsider.id, name="outsider", token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        token_prefix=raw[:8], scopes=["repo"],
    ))
    await db_session.commit()
    denied = await client.get(f"{API}/orgs/{ORG}/installations", headers=auth_headers(raw))
    assert denied.status_code == 403
    assert (await client.get(f"{API}/orgs/no-such-org/installations", headers=auth_headers(admin_token))).status_code == 404


async def test_repo_installation_on_a_private_repository(client, admin_token, test_token):
    """The App's JWT is not a user, so the private-repository gate must not hide the repo."""
    created = await client.post(f"{API}/orgs", json={"login": ORG}, headers=auth_headers(test_token))
    assert created.status_code == 201, created.text
    repo = await client.post(
        f"{API}/orgs/{ORG}/repos", json={"name": "secret", "private": True}, headers=auth_headers(test_token)
    )
    assert repo.status_code == 201, repo.text
    full_name = f"{ORG}/secret"
    selected, selected_headers = await _app(client, admin_token, "2012", "private-selected", [full_name])
    everything, all_headers = await _app(client, admin_token, "2013", "private-all", [])
    _unrelated, unrelated_headers = await _app(client, admin_token, "2014", "private-none", [f"{ORG}/other"])

    got = await client.get(f"{API}/repos/{full_name}/installation", headers=selected_headers)
    assert got.status_code == 200, got.text
    assert got.json()["id"] == selected["id"]
    got = await client.get(f"{API}/repos/{full_name}/installation", headers=all_headers)
    assert got.status_code == 200, got.text
    assert got.json()["id"] == everything["id"]
    assert (await client.get(f"{API}/repos/{full_name}/installation", headers=unrelated_headers)).status_code == 404
    # Without credentials the private repository stays hidden.
    assert (await client.get(f"{API}/repos/{full_name}/installation")).status_code == 404


async def test_all_repositories_installation_does_not_cover_a_missing_repository(client, admin_token, test_token):
    await _org_with_repo(client, test_token)
    _installation, headers = await _app(client, admin_token, "2015", "missing-all", [])

    assert (await client.get(f"{API}/repos/{ORG}/does-not-exist/installation", headers=headers)).status_code == 404
    listed = await client.get(f"{API}/repos/{ORG}/does-not-exist/installations", headers=auth_headers(admin_token))
    assert listed.status_code == 404


async def test_legacy_non_numeric_app_id_does_not_break_responses(client, admin_token, test_token, db_session):
    """Before ids were validated, an App could be stored with a non-numeric id."""
    from sqlalchemy import update

    from app.models.apps import GitHubApp

    full_name = await _org_with_repo(client, test_token)
    await _app(client, admin_token, "2016", "legacy-app", [full_name])
    await db_session.execute(update(GitHubApp).where(GitHubApp.app_id == "2016").values(app_id="legacy-id"))
    await db_session.commit()

    response = await client.get(f"{API}/repos/{full_name}/installations", headers=auth_headers(admin_token))
    assert response.status_code == 200, response.text
    assert response.json()[0]["app_id"] == "legacy-id"
    assert (await client.get(f"{API}/apps/legacy-app")).json()["id"] == "legacy-id"


async def test_org_installation_is_only_for_organizations(client, admin_token, test_token):
    _installation, headers = await _app(client, admin_token, "2017", "user-only-app", [], account="testuser")
    # "testuser" is a user, not an organization.
    assert (await client.get(f"{API}/orgs/testuser/installation", headers=headers)).status_code == 404
    _ghost, ghost_headers = await _app(client, admin_token, "2018", "ghost-app", [], account="ghost-org")
    assert (await client.get(f"{API}/orgs/ghost-org/installation", headers=ghost_headers)).status_code == 404
