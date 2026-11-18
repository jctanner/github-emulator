"""GET/PUT/DELETE /orgs/{org}/teams/{team_slug}/memberships/{username}."""

import pytest

from tests.conftest import API, auth_headers
from tests.test_installation_administration import _bot_token, org  # noqa: F401


async def _org_with_team(client, admin_token, org="acme", team="mint-status"):  # noqa: F811 - not the org fixture
    created = await client.post(
        f"{API}/admin/organizations", headers=auth_headers(admin_token),
        json={"login": org, "admin": "admin"},
    )
    assert created.status_code == 201, created.text
    made = await client.post(f"{API}/orgs/{org}/teams", headers=auth_headers(admin_token), json={"name": team})
    assert made.status_code == 201, made.text
    return made.json()["slug"]


@pytest.mark.asyncio
async def test_member_reads_own_team_membership(client, admin_token, test_user, test_token):
    slug = await _org_with_team(client, admin_token)
    joined = await client.put(f"{API}/orgs/acme/memberships/testuser", headers=auth_headers(admin_token), json={"role": "member"})
    assert joined.status_code == 200, joined.text

    added = await client.put(f"{API}/orgs/acme/teams/{slug}/memberships/testuser", headers=auth_headers(admin_token), json={})
    assert added.status_code == 200, added.text
    assert added.json()["role"] == "member"
    assert added.json()["state"] == "active"

    own = await client.get(f"{API}/orgs/acme/teams/{slug}/memberships/testuser", headers=auth_headers(test_token))
    assert own.status_code == 200, own.text
    assert own.json() == added.json()


@pytest.mark.asyncio
async def test_non_member_is_404_and_removal_takes_effect(client, admin_token, test_user, test_token):
    slug = await _org_with_team(client, admin_token)
    await client.put(f"{API}/orgs/acme/memberships/testuser", headers=auth_headers(admin_token), json={"role": "member"})

    missing = await client.get(f"{API}/orgs/acme/teams/{slug}/memberships/testuser", headers=auth_headers(test_token))
    assert missing.status_code == 404

    await client.put(f"{API}/orgs/acme/teams/{slug}/memberships/testuser", headers=auth_headers(admin_token), json={"role": "maintainer"})
    assert (await client.get(f"{API}/orgs/acme/teams/{slug}/memberships/testuser", headers=auth_headers(test_token))).json()["role"] == "maintainer"

    removed = await client.delete(f"{API}/orgs/acme/teams/{slug}/memberships/testuser", headers=auth_headers(admin_token))
    assert removed.status_code == 204
    gone = await client.get(f"{API}/orgs/acme/teams/{slug}/memberships/testuser", headers=auth_headers(test_token))
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_adding_needs_an_org_admin_and_an_org_member(client, admin_token, test_user, test_token):
    slug = await _org_with_team(client, admin_token)

    not_in_org = await client.put(f"{API}/orgs/acme/teams/{slug}/memberships/testuser", headers=auth_headers(admin_token), json={})
    assert not_in_org.status_code == 422

    await client.put(f"{API}/orgs/acme/memberships/testuser", headers=auth_headers(admin_token), json={"role": "member"})
    by_member = await client.put(f"{API}/orgs/acme/teams/{slug}/memberships/testuser", headers=auth_headers(test_token), json={})
    assert by_member.status_code == 403

    bad_role = await client.put(f"{API}/orgs/acme/teams/{slug}/memberships/testuser", headers=auth_headers(admin_token), json={"role": "owner"})
    assert bad_role.status_code == 422


@pytest.mark.asyncio
async def test_outsider_cannot_read_someone_elses_membership(client, admin_token, test_user, test_token):
    slug = await _org_with_team(client, admin_token)
    # admin is a member of acme (it created it); testuser is not.
    hidden = await client.get(f"{API}/orgs/acme/teams/{slug}/memberships/admin", headers=auth_headers(test_token))
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_a_secret_team_is_hidden_from_org_members_outside_it(client, admin_token, test_user, test_token):
    created = await client.post(
        f"{API}/admin/organizations", headers=auth_headers(admin_token), json={"login": "acme", "admin": "admin"}
    )
    assert created.status_code == 201, created.text
    made = await client.post(
        f"{API}/orgs/acme/teams", headers=auth_headers(admin_token), json={"name": "security", "privacy": "secret"}
    )
    assert made.status_code == 201, made.text
    slug = made.json()["slug"]
    await client.put(f"{API}/orgs/acme/memberships/testuser", headers=auth_headers(admin_token), json={"role": "member"})
    added = await client.put(f"{API}/orgs/acme/teams/{slug}/memberships/admin", headers=auth_headers(admin_token), json={})
    assert added.status_code == 200, added.text
    url = f"{API}/orgs/acme/teams/{slug}/memberships/admin"

    # An org member outside a secret team cannot see who is in it.
    assert (await client.get(url, headers=auth_headers(test_token))).status_code == 404

    joined = await client.put(f"{API}/orgs/acme/teams/{slug}/memberships/testuser", headers=auth_headers(admin_token), json={})
    assert joined.status_code == 200, joined.text
    assert (await client.get(url, headers=auth_headers(test_token))).status_code == 200


@pytest.mark.asyncio
async def test_a_team_maintainer_manages_their_own_team_only(client, admin_token, test_user, test_token):
    slug = await _org_with_team(client, admin_token)
    other = await client.post(f"{API}/orgs/acme/teams", headers=auth_headers(admin_token), json={"name": "other"})
    assert other.status_code == 201, other.text
    await client.put(f"{API}/orgs/acme/memberships/testuser", headers=auth_headers(admin_token), json={"role": "member"})
    made = await client.put(
        f"{API}/orgs/acme/teams/{slug}/memberships/testuser", headers=auth_headers(admin_token), json={"role": "maintainer"}
    )
    assert made.status_code == 200, made.text

    # A maintainer adds, changes and removes members of their own team.
    added = await client.put(f"{API}/orgs/acme/teams/{slug}/memberships/admin", headers=auth_headers(test_token), json={})
    assert added.status_code == 200, added.text
    changed = await client.put(
        f"{API}/orgs/acme/teams/{slug}/memberships/admin", headers=auth_headers(test_token), json={"role": "maintainer"}
    )
    assert changed.json()["role"] == "maintainer"
    removed = await client.delete(f"{API}/orgs/acme/teams/{slug}/memberships/admin", headers=auth_headers(test_token))
    assert removed.status_code == 204

    # Not another team's.
    elsewhere = await client.put(
        f"{API}/orgs/acme/teams/{other.json()['slug']}/memberships/admin", headers=auth_headers(test_token), json={}
    )
    assert elsewhere.status_code == 403


@pytest.mark.asyncio
async def test_a_maintainer_removed_from_the_org_manages_nothing(client, admin_token, test_user, test_token):
    slug = await _org_with_team(client, admin_token)
    await client.put(f"{API}/orgs/acme/memberships/testuser", headers=auth_headers(admin_token), json={"role": "member"})
    made = await client.put(
        f"{API}/orgs/acme/teams/{slug}/memberships/testuser", headers=auth_headers(admin_token), json={"role": "maintainer"}
    )
    assert made.status_code == 200, made.text
    removed = await client.delete(f"{API}/orgs/acme/memberships/testuser", headers=auth_headers(admin_token))
    assert removed.status_code == 204, removed.text

    # Leaving the organization took the team membership with it.
    gone = await client.get(f"{API}/orgs/acme/teams/{slug}/memberships/testuser", headers=auth_headers(admin_token))
    assert gone.status_code == 404
    url = f"{API}/orgs/acme/teams/{slug}/memberships/admin"
    assert (await client.put(url, headers=auth_headers(test_token), json={})).status_code == 403
    assert (await client.delete(url, headers=auth_headers(test_token))).status_code == 403

    # Rejoining the organization does not bring the maintainer role back.
    await client.put(f"{API}/orgs/acme/memberships/testuser", headers=auth_headers(admin_token), json={"role": "member"})
    assert (await client.put(url, headers=auth_headers(test_token), json={})).status_code == 403


@pytest.mark.asyncio
async def test_an_app_reads_and_changes_team_memberships_by_its_members_grant(
    client, org, admin_token, test_user, test_token,  # noqa: F811 - the org fixture
):
    """An App's bot has no organization membership: its installation's
    ``members`` permission decides, as for organization memberships."""
    member_org = org
    made = await client.post(f"{API}/orgs/{member_org}/teams", headers=auth_headers(test_token), json={"name": "crew"})
    assert made.status_code == 201, made.text
    slug = made.json()["slug"]
    joined = await client.put(f"{API}/orgs/{member_org}/memberships/admin", headers=auth_headers(test_token), json={"role": "member"})
    assert joined.status_code == 200, joined.text
    url = f"{API}/orgs/{member_org}/teams/{slug}/memberships/admin"

    reader = await _bot_token(client, admin_token, "3001", "members-reader", {"members": "read", "metadata": "read"})
    writer = await _bot_token(client, admin_token, "3002", "members-writer", {"members": "write", "metadata": "read"})
    other = await _bot_token(client, admin_token, "3003", "contents-only", {"contents": "write", "metadata": "read"})

    assert (await client.put(url, headers=writer, json={"role": "maintainer"})).json()["role"] == "maintainer"
    seen = await client.get(url, headers=reader)
    assert seen.status_code == 200, seen.text
    assert seen.json()["role"] == "maintainer"
    refused = await client.put(url, headers=reader, json={})
    assert refused.status_code == 403
    assert "Resource not accessible by integration" in refused.json()["message"]
    assert (await client.put(url, headers=other, json={})).status_code == 403
    assert (await client.delete(url, headers=writer)).status_code == 204
