"""An App installed on an organization acts there by its permissions.

GitHub authorizes an App's bot by its installation, never by membership:
``administration: write`` creates, changes and deletes the organization's
repositories; ``members: read`` reads its memberships;
``organization_administration: read`` lists its installations. Fullsend's
behaviour suite acts as such an App (its e2e App) to lease slot repositories.
"""

import pytest
import pytest_asyncio

from tests.conftest import API, auth_headers
from tests.test_org_memberships_api import _make_user

ORG = "app-org"
FULL = {"administration": "write", "contents": "write", "metadata": "read",
        "members": "read", "organization_administration": "read"}


@pytest_asyncio.fixture
async def org(client, test_token):
    resp = await client.post(f"{API}/orgs", json={"login": ORG}, headers=auth_headers(test_token))
    assert resp.status_code == 201
    return ORG


async def _bot_token(client, admin_token, app_id, slug, permissions):
    created = await client.post(f"{API}/admin/apps", headers=auth_headers(admin_token),
                                json={"app_id": app_id, "name": slug, "slug": slug, "permissions": permissions})
    assert created.status_code == 201
    installation = await client.post(f"{API}/admin/apps/{app_id}/installations", headers=auth_headers(admin_token),
                                     json={"account_login": ORG, "account_type": "Organization", "repositories": []})
    assert installation.status_code == 201
    issued = await client.post(f"{API}/admin/apps/{app_id}/installations/{installation.json()['id']}/access_tokens",
                               headers=auth_headers(admin_token), json={})
    assert issued.status_code == 201
    return {"Authorization": f"token {issued.json()['token']}"}


@pytest.mark.asyncio
async def test_administration_write_creates_changes_and_deletes_repositories(client, org, admin_token, test_token):
    bot = await _bot_token(client, admin_token, "2001", "e2e-app", FULL)
    created = await client.post(f"{API}/orgs/{org}/repos", json={"name": "slot", "auto_init": True}, headers=bot)
    assert created.status_code == 201, created.text
    assert created.json()["owner"]["login"] == org
    patched = await client.patch(f"{API}/repos/{org}/slot", json={"description": "leased"}, headers=bot)
    assert patched.status_code == 200 and patched.json()["description"] == "leased"
    assert (await client.delete(f"{API}/repos/{org}/slot", headers=bot)).status_code == 204

    # A repository a member created, too.
    resp = await client.post(f"{API}/orgs/{org}/repos", json={"name": "by-member"}, headers=auth_headers(test_token))
    assert resp.status_code == 201
    assert (await client.patch(f"{API}/repos/{org}/by-member", json={"has_wiki": False}, headers=bot)).status_code == 200
    protection = await client.put(
        f"{API}/repos/{org}/by-member/branches/main/protection", headers=bot,
        json={"required_status_checks": None, "enforce_admins": False,
              "required_pull_request_reviews": None, "restrictions": None},
    )
    assert protection.status_code in (200, 404), protection.text  # 404: no main branch yet
    assert (await client.delete(f"{API}/repos/{org}/by-member", headers=bot)).status_code == 204


@pytest.mark.asyncio
async def test_without_administration_the_app_cannot(client, org, admin_token, test_token):
    bot = await _bot_token(client, admin_token, "2002", "contents-app", {"contents": "write", "metadata": "read"})
    created = await client.post(f"{API}/orgs/{org}/repos", json={"name": "nope"}, headers=bot)
    assert created.status_code == 403
    assert "Resource not accessible by integration" in created.json()["message"]
    resp = await client.post(f"{API}/orgs/{org}/repos", json={"name": "kept", "auto_init": True},
                             headers=auth_headers(test_token))
    assert resp.status_code == 201
    for call in (client.patch(f"{API}/repos/{org}/kept", json={"description": "x"}, headers=bot),
                 client.delete(f"{API}/repos/{org}/kept", headers=bot),
                 client.put(f"{API}/repos/{org}/kept/actions/permissions/workflow", headers=bot,
                            json={"default_workflow_permissions": "read"}),
                 client.put(f"{API}/repos/{org}/kept/branches/main/protection", headers=bot,
                            json={"required_status_checks": None, "enforce_admins": False,
                                  "required_pull_request_reviews": None, "restrictions": None})):
        resp = await call
        assert resp.status_code == 403, resp.text
    # Reading the repository is metadata, which every installation has.
    assert (await client.get(f"{API}/repos/{org}/kept", headers=bot)).status_code == 200


@pytest.mark.asyncio
async def test_an_app_on_another_account_creates_nothing_here(client, org, admin_token, test_token):
    other = await client.post(f"{API}/orgs", json={"login": "other-org"}, headers=auth_headers(test_token))
    assert other.status_code == 201
    bot = await _bot_token(client, admin_token, "2003", "elsewhere-app", FULL)
    assert (await client.post(f"{API}/orgs/other-org/repos", json={"name": "x"}, headers=bot)).status_code == 403
    assert (await client.get(f"{API}/orgs/other-org/installations", headers=bot)).status_code == 403


@pytest.mark.asyncio
async def test_members_read_and_organization_administration_read(client, db_session, org, admin_token, test_token):
    await _make_user(db_session, "someone")
    resp = await client.put(f"{API}/orgs/{org}/memberships/someone", json={"role": "member"},
                            headers=auth_headers(test_token))
    assert resp.status_code == 200
    bot = await _bot_token(client, admin_token, "2004", "reader-app", FULL)
    membership = await client.get(f"{API}/orgs/{org}/memberships/someone", headers=bot)
    assert membership.status_code == 200 and membership.json()["state"] == "active"
    installations = await client.get(f"{API}/orgs/{org}/installations", headers=bot)
    assert installations.status_code == 200 and installations.json()["total_count"] == 1

    plain = await _bot_token(client, admin_token, "2005", "plain-app", {"contents": "read", "metadata": "read"})
    assert (await client.get(f"{API}/orgs/{org}/memberships/someone", headers=plain)).status_code == 403
    assert (await client.get(f"{API}/orgs/{org}/installations", headers=plain)).status_code == 403


@pytest.mark.asyncio
async def test_a_narrowed_token_stays_narrow_as_a_basic_password(client, org, admin_token, test_token):
    """git sends an installation token as x-access-token:<token>; it must be
    scoped by its own permissions there too, not by its installation's."""
    import base64

    created = await client.post(f"{API}/admin/apps", headers=auth_headers(admin_token),
                                json={"app_id": "2006", "name": "narrow", "slug": "narrow", "permissions": FULL})
    assert created.status_code == 201
    installation = await client.post(f"{API}/admin/apps/2006/installations", headers=auth_headers(admin_token),
                                     json={"account_login": ORG, "account_type": "Organization", "repositories": []})
    narrow = await client.post(f"{API}/admin/apps/2006/installations/{installation.json()['id']}/access_tokens",
                               headers=auth_headers(admin_token),
                               json={"permissions": {"contents": "read", "metadata": "read"}})
    token = narrow.json()["token"]
    resp = await client.post(f"{API}/orgs/{org}/repos", json={"name": "kept"}, headers=auth_headers(test_token))
    assert resp.status_code == 201
    basic = {"Authorization": "Basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()}
    assert (await client.patch(f"{API}/repos/{org}/kept", json={"description": "x"}, headers=basic)).status_code == 403
    assert (await client.delete(f"{API}/repos/{org}/kept", headers=basic)).status_code == 403
    assert (await client.get(f"{API}/repos/{org}/kept", headers=basic)).status_code == 200


@pytest.mark.asyncio
async def test_creating_a_repository_gives_the_bot_no_repository_role(client, org, admin_token):
    """The App administers the repository through its installation, but its
    bot is not reported as an admin collaborator, as on GitHub."""
    bot = await _bot_token(client, admin_token, "2007", "creator-app", FULL)
    created = await client.post(f"{API}/orgs/{org}/repos", json={"name": "made-by-bot", "auto_init": True}, headers=bot)
    assert created.status_code == 201
    permission = await client.get(f"{API}/repos/{org}/made-by-bot/collaborators/creator-app[bot]/permission",
                                  headers=auth_headers(admin_token))
    assert permission.status_code == 404, permission.text
    # It still administers and deletes the repository through the installation.
    assert (await client.patch(f"{API}/repos/{org}/made-by-bot", json={"description": "x"}, headers=bot)).status_code == 200
    assert (await client.delete(f"{API}/repos/{org}/made-by-bot", headers=bot)).status_code == 204
