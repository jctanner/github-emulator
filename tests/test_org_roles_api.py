"""Organization roles: the predefined all-repository roles and what they grant.

A role assigned to an organization member grants its permission on every
repository the organization owns, including ones created afterwards. That is
how Fullsend's behaviour suite gives its write and triage test actors access
to slot repositories it deletes and recreates per lease (BT-E4).
"""

import base64

import pytest
import pytest_asyncio

from tests.conftest import auth_headers
from tests.test_org_memberships_api import _make_user

API = "/api/v3"
READ, TRIAGE, WRITE, MAINTAIN, ADMIN = 8132, 8133, 8134, 8135, 8136


@pytest_asyncio.fixture
async def org(client, test_user, test_token):
    """An org owned by testuser (POST /orgs makes the caller an admin)."""
    resp = await client.post(f"{API}/orgs", json={"login": "role-org"}, headers=auth_headers(test_token))
    assert resp.status_code == 201
    return "role-org"


@pytest_asyncio.fixture
async def writer(db_session):
    return await _make_user(db_session, "writer")


@pytest_asyncio.fixture
async def triager(db_session):
    return await _make_user(db_session, "triager")


async def _join(client, org, login, owner_token):
    resp = await client.put(f"{API}/orgs/{org}/memberships/{login}", json={"role": "member"},
                            headers=auth_headers(owner_token))
    assert resp.status_code == 200


async def _assign(client, org, login, role_id, token):
    return await client.put(f"{API}/orgs/{org}/organization-roles/users/{login}/{role_id}",
                            headers=auth_headers(token))


async def _new_repo(client, org, name, owner_token):
    resp = await client.post(f"{API}/orgs/{org}/repos", json={"name": name}, headers=auth_headers(owner_token))
    assert resp.status_code == 201
    return f"{org}/{name}"


async def _permission(client, repo, login, token):
    return await client.get(f"{API}/repos/{repo}/collaborators/{login}/permission", headers=auth_headers(token))


async def _write_file(client, repo, path, token):
    return await client.put(
        f"{API}/repos/{repo}/contents/{path}",
        json={"message": "add", "content": base64.b64encode(b"hello\n").decode()},
        headers=auth_headers(token),
    )


@pytest.mark.asyncio
async def test_lists_the_predefined_roles_by_github_name(client, org, test_token):
    resp = await client.get(f"{API}/orgs/{org}/organization-roles", headers=auth_headers(test_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 5
    names = {role["name"]: role["id"] for role in body["roles"]}
    assert names == {"all_repo_read": READ, "all_repo_triage": TRIAGE, "all_repo_write": WRITE,
                     "all_repo_maintain": MAINTAIN, "all_repo_admin": ADMIN}
    assert all(role["source"] == "Predefined" for role in body["roles"])
    one = await client.get(f"{API}/orgs/{org}/organization-roles/{WRITE}", headers=auth_headers(test_token))
    assert one.status_code == 200 and one.json()["base_role"] == "write"
    missing = await client.get(f"{API}/orgs/{org}/organization-roles/1", headers=auth_headers(test_token))
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_only_an_org_admin_reads_roles_and_their_holders(client, org, test_token, writer):
    paths = ("organization-roles", f"organization-roles/{ADMIN}",
             f"organization-roles/{ADMIN}/users", f"organization-roles/{ADMIN}/teams")
    # Neither an outsider nor an ordinary member.
    for path in paths:
        assert (await client.get(f"{API}/orgs/{org}/{path}", headers=auth_headers(writer))).status_code == 403
    await _join(client, org, "writer", test_token)
    for path in paths:
        assert (await client.get(f"{API}/orgs/{org}/{path}", headers=auth_headers(writer))).status_code == 403


@pytest.mark.asyncio
async def test_assignment_needs_an_org_admin_and_a_member(client, org, test_token, writer, triager):
    # Not yet a member.
    assert (await _assign(client, org, "writer", WRITE, test_token)).status_code == 422
    await _join(client, org, "writer", test_token)
    await _join(client, org, "triager", test_token)
    # A member who is not an org admin may not assign roles.
    assert (await _assign(client, org, "triager", WRITE, writer)).status_code == 403
    # Assigning twice is idempotent.
    assert (await _assign(client, org, "writer", WRITE, test_token)).status_code == 204
    assert (await _assign(client, org, "writer", WRITE, test_token)).status_code == 204
    holders = await client.get(f"{API}/orgs/{org}/organization-roles/{WRITE}/users", headers=auth_headers(test_token))
    assert [(u["login"], u["assignment"]) for u in holders.json()] == [("writer", "direct")]
    teams = await client.get(f"{API}/orgs/{org}/organization-roles/{WRITE}/teams", headers=auth_headers(test_token))
    assert teams.json() == []


@pytest.mark.asyncio
async def test_a_role_covers_repositories_created_after_it(client, org, test_token, writer, triager):
    await _join(client, org, "writer", test_token)
    await _join(client, org, "triager", test_token)
    assert (await _assign(client, org, "writer", WRITE, test_token)).status_code == 204
    assert (await _assign(client, org, "triager", TRIAGE, test_token)).status_code == 204
    repo = await _new_repo(client, org, "created-later", test_token)

    written = (await _permission(client, repo, "writer", test_token)).json()
    assert (written["permission"], written["role_name"]) == ("write", "write")
    assert written["user"]["permissions"]["push"] is True
    triaged = (await _permission(client, repo, "triager", test_token)).json()
    # GitHub's legacy field reads triage as read; role_name names it.
    assert (triaged["permission"], triaged["role_name"]) == ("read", "triage")
    owner = (await _permission(client, repo, "testuser", test_token)).json()
    assert owner["role_name"] == "admin"

    # Write reaches content writes; triage does not.
    assert (await _write_file(client, repo, "by-writer.txt", writer)).status_code == 201
    assert (await _write_file(client, repo, "by-triager.txt", triager)).status_code == 403


@pytest.mark.asyncio
async def test_a_member_without_a_role_has_no_repository_permission(client, org, test_token, writer):
    await _join(client, org, "writer", test_token)
    repo = await _new_repo(client, org, "no-role", test_token)
    assert (await _permission(client, repo, "writer", test_token)).status_code == 404
    assert (await _write_file(client, repo, "x.txt", writer)).status_code == 403


@pytest.mark.asyncio
async def test_the_higher_of_collaborator_and_role_wins(client, org, test_token, writer):
    await _join(client, org, "writer", test_token)
    await _assign(client, org, "writer", TRIAGE, test_token)
    repo = await _new_repo(client, org, "mixed", test_token)
    resp = await client.put(f"{API}/repos/{repo}/collaborators/writer", json={"permission": "maintain"},
                            headers=auth_headers(test_token))
    assert resp.status_code in (201, 204)
    assert (await _permission(client, repo, "writer", test_token)).json()["role_name"] == "maintain"


@pytest.mark.asyncio
async def test_removing_a_role_or_the_membership_takes_the_access_away(client, org, test_token, writer):
    await _join(client, org, "writer", test_token)
    await _assign(client, org, "writer", WRITE, test_token)
    repo = await _new_repo(client, org, "revoked", test_token)
    assert (await _permission(client, repo, "writer", test_token)).status_code == 200

    resp = await client.delete(f"{API}/orgs/{org}/organization-roles/users/writer/{WRITE}",
                               headers=auth_headers(test_token))
    assert resp.status_code == 204
    assert (await _permission(client, repo, "writer", test_token)).status_code == 404

    await _assign(client, org, "writer", WRITE, test_token)
    resp = await client.delete(f"{API}/orgs/{org}/memberships/writer", headers=auth_headers(test_token))
    assert resp.status_code == 204
    assert (await _permission(client, repo, "writer", test_token)).status_code == 404
    holders = await client.get(f"{API}/orgs/{org}/organization-roles/{WRITE}/users", headers=auth_headers(test_token))
    assert holders.json() == []
    # Rejoining does not bring the role back.
    await _join(client, org, "writer", test_token)
    assert (await _permission(client, repo, "writer", test_token)).status_code == 404


@pytest.mark.asyncio
async def test_remove_all_roles(client, org, test_token, writer):
    await _join(client, org, "writer", test_token)
    await _assign(client, org, "writer", WRITE, test_token)
    await _assign(client, org, "writer", READ, test_token)
    resp = await client.delete(f"{API}/orgs/{org}/organization-roles/users/writer", headers=auth_headers(test_token))
    assert resp.status_code == 204
    for role_id in (WRITE, READ):
        holders = await client.get(f"{API}/orgs/{org}/organization-roles/{role_id}/users",
                                   headers=auth_headers(test_token))
        assert holders.json() == []


@pytest.mark.asyncio
async def test_an_org_owner_administers_repositories_another_member_created(client, org, test_token, writer):
    """An organization owner has admin on every repository the organization
    owns, not only the ones they created."""
    await _join(client, org, "writer", test_token)
    await _assign(client, org, "writer", WRITE, test_token)
    resp = await client.post(f"{API}/orgs/{org}/repos", json={"name": "by-writer"}, headers=auth_headers(writer))
    if resp.status_code != 201:
        pytest.skip(f"members cannot create organization repositories here: HTTP {resp.status_code}")
    repo = f"{org}/by-writer"
    assert (await _permission(client, repo, "testuser", test_token)).json()["role_name"] == "admin"
    assert (await _permission(client, repo, "writer", test_token)).json()["role_name"] == "admin"


@pytest.mark.asyncio
async def test_a_member_reads_the_organizations_private_repositories(client, db_session, org, test_token, writer, triager):
    """Membership is matched on the repository's organization, not on the id
    of the user who created it."""
    from sqlalchemy import select

    from app.models.organization import Organization
    from app.models.user import User

    # writer, an org owner, creates the repository, so its owner_id is
    # writer's id; make sure that differs from the organization's.
    resp = await client.put(f"{API}/orgs/{org}/memberships/writer", json={"role": "admin"},
                            headers=auth_headers(test_token))
    assert resp.status_code == 200
    await _join(client, org, "triager", test_token)
    org_id = (await db_session.execute(select(Organization.id).where(Organization.login == org))).scalar_one()
    writer_id = (await db_session.execute(select(User.id).where(User.login == "writer"))).scalar_one()
    assert writer_id != org_id
    resp = await client.post(f"{API}/orgs/{org}/repos", json={"name": "secret", "private": True},
                             headers=auth_headers(writer))
    assert resp.status_code == 201
    assert (await client.get(f"{API}/repos/{org}/secret", headers=auth_headers(triager))).status_code == 200


@pytest.mark.asyncio
async def test_an_admin_role_reaches_repository_settings(client, org, test_token, writer, triager):
    """all_repo_admin administers the repository's settings like any admin;
    a lower role does not."""
    await _join(client, org, "writer", test_token)
    await _join(client, org, "triager", test_token)
    await _assign(client, org, "writer", ADMIN, test_token)
    await _assign(client, org, "triager", WRITE, test_token)
    repo = await _new_repo(client, org, "settings", test_token)
    resp = await client.patch(f"{API}/repos/{repo}", json={"description": "by an admin role"},
                              headers=auth_headers(writer))
    assert resp.status_code == 200 and resp.json()["description"] == "by an admin role"
    resp = await client.patch(f"{API}/repos/{repo}", json={"description": "by write"},
                              headers=auth_headers(triager))
    assert resp.status_code == 403
