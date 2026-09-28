"""A GitHub App's bot has the access its installation grants.

An installation token acts as the App's bot user, and on GitHub that bot's
access to a repository is what the installation carries, not a collaborator
row. The emulator answered "not a collaborator" for every bot: the
permission endpoint returned 404 and a git push was refused, so an
installation token with contents: write could not do what it was minted for.
Breadboard's onboarding button is the consumer: Fullsend's CLI asks the
permission endpoint whether the authenticated identity may push before it
commits a scaffold.
"""

import time

import pytest
from jose import jwt

from app.git.smart_http import _check_write_access
from app.models.user import User
from app.services.repository_access import installation_role
from tests.conftest import API, auth_headers
from sqlalchemy import select
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


async def _app(client, admin_token, app_id, slug, permissions, repositories):
    created = await client.post(
        f"{API}/admin/apps", headers=auth_headers(admin_token),
        json={"app_id": app_id, "name": slug, "slug": slug, "permissions": permissions},
    )
    assert created.status_code == 201, created.text
    app = created.json()
    installation = await client.post(
        f"{API}/admin/apps/{app_id}/installations", headers=auth_headers(admin_token),
        json={"account_login": "testuser", "account_type": "User", "repositories": repositories,
              "permissions": permissions},
    )
    assert installation.status_code == 201, installation.text
    now = int(time.time())
    app_jwt = jwt.encode({"iss": app_id, "iat": now - 10, "exp": now + 60}, app["private_key"], algorithm="RS256")
    return installation.json()["id"], {"Authorization": f"Bearer {app_jwt}"}


async def test_an_all_repositories_installation_covers_the_accounts_repositories(client, admin_token, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    installation_id, headers = await _app(client, admin_token, "2001", "onboarder", {"contents": "write"}, [])
    minted = await client.post(f"{API}/app/installations/{installation_id}/access_tokens", headers=headers,
                               json={"repositories": [repo]})
    assert minted.status_code == 201, minted.text
    assert minted.json()["repositories"] == [{"full_name": f"{owner}/{repo}"}]

    elsewhere = await client.post(f"{API}/app/installations/{installation_id}/access_tokens", headers=headers,
                                  json={"repositories": ["someone-else/repo"]})
    assert elsewhere.status_code == 422


async def test_the_permission_endpoint_reports_the_installations_grant(client, admin_token, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    await _app(client, admin_token, "2002", "writer", {"contents": "write"}, [])
    resp = await client.get(f"{API}/repos/{owner}/{repo}/collaborators/writer[bot]/permission", headers=auth_headers(test_token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["role_name"] == "write"
    assert resp.json()["user"]["login"] == "writer[bot]"


async def test_a_read_only_installation_reports_read_and_cannot_push(client, admin_token, db_session, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    await _app(client, admin_token, "2003", "reader", {"contents": "read"}, [f"{owner}/{repo}"])
    resp = await client.get(f"{API}/repos/{owner}/{repo}/collaborators/reader[bot]/permission", headers=auth_headers(test_token))
    assert resp.json()["role_name"] == "read"

    from app.models.repository import Repository
    repository = (await db_session.execute(select(Repository).where(Repository.full_name == f"{owner}/{repo}"))).scalar_one()
    bot = (await db_session.execute(select(User).where(User.login == "reader[bot]"))).scalar_one()
    assert await installation_role(db_session, repository, bot) == "read"
    with pytest.raises(HTTPException) as refused:
        await _check_write_access(repository, bot, db_session)
    assert refused.value.status_code == 403


async def test_a_write_installation_may_push(client, admin_token, db_session, test_repo_with_init):
    owner, repo, _ = test_repo_with_init
    await _app(client, admin_token, "2004", "pusher", {"contents": "write"}, [])
    from app.models.repository import Repository
    repository = (await db_session.execute(select(Repository).where(Repository.full_name == f"{owner}/{repo}"))).scalar_one()
    bot = (await db_session.execute(select(User).where(User.login == "pusher[bot]"))).scalar_one()
    await _check_write_access(repository, bot, db_session)  # no exception


async def test_an_installation_elsewhere_grants_nothing_here(client, admin_token, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    other = await client.post(f"{API}/user/repos", json={"name": "other", "auto_init": True}, headers=auth_headers(test_token))
    assert other.status_code == 201
    await _app(client, admin_token, "2005", "narrow", {"contents": "write"}, [f"{owner}/other"])
    resp = await client.get(f"{API}/repos/{owner}/{repo}/collaborators/narrow[bot]/permission", headers=auth_headers(test_token))
    assert resp.status_code == 404


async def test_a_taken_app_id_is_a_conflict_not_a_server_error(client, admin_token):
    first = await client.post(f"{API}/admin/apps", headers=auth_headers(admin_token),
                              json={"app_id": "2100", "name": "one", "slug": "one", "permissions": {}})
    assert first.status_code == 201
    second = await client.post(f"{API}/admin/apps", headers=auth_headers(admin_token),
                               json={"app_id": "2100", "name": "two", "slug": "two", "permissions": {}})
    assert second.status_code == 409
    assert "id" in second.json()["message"].lower() or "id" in second.text.lower()
