"""Tests for the organization membership endpoints."""

import hashlib
import secrets

import pytest
import pytest_asyncio

from tests.conftest import auth_headers

API = "/api/v3"


async def _make_user(db_session, login: str, site_admin: bool = False) -> str:
    """Create a user with a token and return the raw token."""
    from app.models.token import PersonalAccessToken
    from app.models.user import User

    user = User(
        login=login,
        hashed_password=hashlib.sha256(b"password").hexdigest(),
        email=f"{login}@test.com",
        site_admin=site_admin,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    raw = f"ghp_{secrets.token_hex(20)}"
    db_session.add(PersonalAccessToken(
        user_id=user.id,
        name=f"{login}-token",
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        token_prefix=raw[:8],
        scopes=["repo", "user", "admin:org"],
    ))
    await db_session.commit()
    return raw


@pytest_asyncio.fixture
async def other_token(db_session):
    return await _make_user(db_session, "otheruser")


@pytest_asyncio.fixture
async def org(client, test_user, test_token):
    """An org owned by testuser (POST /orgs makes the caller an admin)."""
    resp = await client.post(
        f"{API}/orgs", json={"login": "member-org"}, headers=auth_headers(test_token)
    )
    assert resp.status_code == 201
    return "member-org"


@pytest_asyncio.fixture
async def memberless_org(db_session):
    """An org with no members, as the admin API used to create them."""
    from app.models.organization import Organization

    db_session.add(Organization(login="empty-org"))
    await db_session.commit()
    return "empty-org"


@pytest.mark.asyncio
async def test_add_member_and_read_membership(client, org, test_token, other_token):
    resp = await client.put(
        f"{API}/orgs/{org}/memberships/otheruser",
        json={"role": "member"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "active"
    assert data["role"] == "member"
    assert data["user"]["login"] == "otheruser"
    assert data["organization"]["login"] == org
    assert data["url"].endswith(f"/orgs/{org}/memberships/otheruser")

    resp = await client.get(
        f"{API}/orgs/{org}/memberships/otheruser", headers=auth_headers(test_token)
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "member"

    assert (await client.get(f"{API}/orgs/{org}/members/otheruser")).status_code == 204
    logins = [m["login"] for m in (await client.get(f"{API}/orgs/{org}/members")).json()]
    assert logins == ["testuser", "otheruser"]


@pytest.mark.asyncio
async def test_role_defaults_to_member_and_can_be_promoted(client, org, test_token, other_token):
    resp = await client.put(
        f"{API}/orgs/{org}/memberships/otheruser", headers=auth_headers(test_token)
    )
    assert resp.json()["role"] == "member"

    resp = await client.put(
        f"{API}/orgs/{org}/memberships/otheruser",
        json={"role": "admin"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"

    admins = (await client.get(f"{API}/orgs/{org}/members", params={"role": "admin"})).json()
    assert sorted(m["login"] for m in admins) == ["otheruser", "testuser"]
    members = (await client.get(f"{API}/orgs/{org}/members", params={"role": "member"})).json()
    assert members == []


@pytest.mark.asyncio
async def test_added_member_can_create_org_repository(client, org, test_token, other_token):
    resp = await client.post(
        f"{API}/orgs/{org}/repos", json={"name": "before"}, headers=auth_headers(other_token)
    )
    assert resp.status_code == 403

    await client.put(
        f"{API}/orgs/{org}/memberships/otheruser",
        json={"role": "member"},
        headers=auth_headers(test_token),
    )
    resp = await client.post(
        f"{API}/orgs/{org}/repos", json={"name": "after"}, headers=auth_headers(other_token)
    )
    assert resp.status_code == 201
    assert resp.json()["full_name"] == f"{org}/after"


@pytest.mark.asyncio
async def test_remove_member(client, org, test_token, other_token):
    headers = auth_headers(test_token)
    await client.put(f"{API}/orgs/{org}/memberships/otheruser", headers=headers)

    resp = await client.delete(f"{API}/orgs/{org}/memberships/otheruser", headers=headers)
    assert resp.status_code == 204
    assert (await client.get(f"{API}/orgs/{org}/members/otheruser")).status_code == 404

    await client.put(f"{API}/orgs/{org}/memberships/otheruser", headers=headers)
    resp = await client.delete(f"{API}/orgs/{org}/members/otheruser", headers=headers)
    assert resp.status_code == 204
    resp = await client.get(f"{API}/orgs/{org}/memberships/otheruser", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_last_admin_cannot_be_removed_or_demoted(client, org, test_token, other_token):
    headers = auth_headers(test_token)
    resp = await client.put(
        f"{API}/orgs/{org}/memberships/testuser", json={"role": "member"}, headers=headers
    )
    assert resp.status_code == 422
    resp = await client.delete(f"{API}/orgs/{org}/memberships/testuser", headers=headers)
    assert resp.status_code == 422

    # With a second admin, the first may step down.
    await client.put(
        f"{API}/orgs/{org}/memberships/otheruser", json={"role": "admin"}, headers=headers
    )
    resp = await client.put(
        f"{API}/orgs/{org}/memberships/testuser", json={"role": "member"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "member"


@pytest.mark.asyncio
async def test_own_memberships(client, org, test_token, other_token):
    await client.put(f"{API}/orgs/{org}/memberships/otheruser", headers=auth_headers(test_token))

    resp = await client.get(f"{API}/user/memberships/orgs", headers=auth_headers(other_token))
    assert resp.status_code == 200
    assert [(m["organization"]["login"], m["role"]) for m in resp.json()] == [(org, "member")]

    resp = await client.get(
        f"{API}/user/memberships/orgs/{org}", headers=auth_headers(other_token)
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "active"

    resp = await client.get(
        f"{API}/user/memberships/orgs", params={"state": "pending"},
        headers=auth_headers(other_token),
    )
    assert resp.json() == []

    resp = await client.get(f"{API}/user/memberships/orgs/no-such-org", headers=auth_headers(other_token))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pending_membership_can_be_accepted(client, db_session, org, other_token):
    from sqlalchemy import select

    from app.models.organization import Organization, OrgMembership
    from app.models.user import User

    org_row = (await db_session.execute(select(Organization).where(Organization.login == org))).scalar_one()
    user_row = (await db_session.execute(select(User).where(User.login == "otheruser"))).scalar_one()
    db_session.add(OrgMembership(org_id=org_row.id, user_id=user_row.id, role="member", state="pending"))
    await db_session.commit()

    headers = auth_headers(other_token)
    assert (await client.get(f"{API}/orgs/{org}/members/otheruser")).status_code == 404

    resp = await client.patch(
        f"{API}/user/memberships/orgs/{org}", json={"state": "pending"}, headers=headers
    )
    assert resp.status_code == 422

    resp = await client.patch(
        f"{API}/user/memberships/orgs/{org}", json={"state": "active"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "active"
    assert (await client.get(f"{API}/orgs/{org}/members/otheruser")).status_code == 204


@pytest.mark.asyncio
async def test_site_admin_can_add_owner_to_memberless_org(
    client, memberless_org, admin_token, test_user, test_token
):
    resp = await client.put(
        f"{API}/orgs/{memberless_org}/memberships/testuser",
        json={"role": "admin"},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"

    resp = await client.post(
        f"{API}/orgs/{memberless_org}/repos",
        json={"name": "repaired"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_permission_and_validation_errors(client, org, test_token, other_token):
    # A non-member, and then a plain member, may not manage memberships.
    resp = await client.put(
        f"{API}/orgs/{org}/memberships/otheruser", headers=auth_headers(other_token)
    )
    assert resp.status_code == 403
    resp = await client.get(
        f"{API}/orgs/{org}/memberships/testuser", headers=auth_headers(other_token)
    )
    assert resp.status_code == 403

    await client.put(f"{API}/orgs/{org}/memberships/otheruser", headers=auth_headers(test_token))
    resp = await client.delete(
        f"{API}/orgs/{org}/memberships/testuser", headers=auth_headers(other_token)
    )
    assert resp.status_code == 403

    headers = auth_headers(test_token)
    resp = await client.put(
        f"{API}/orgs/{org}/memberships/otheruser", json={"role": "owner"}, headers=headers
    )
    assert resp.status_code == 422
    resp = await client.put(f"{API}/orgs/{org}/memberships/nobody", headers=headers)
    assert resp.status_code == 404
    resp = await client.put(f"{API}/orgs/no-such-org/memberships/otheruser", headers=headers)
    assert resp.status_code == 404
    resp = await client.get(f"{API}/orgs/{org}/members", params={"role": "owner"})
    assert resp.status_code == 422

    resp = await client.put(f"{API}/orgs/{org}/memberships/otheruser")
    assert resp.status_code == 401
