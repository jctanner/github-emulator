"""A user with no access to a repository still has a permission: none.

GitHub's ``GET /repos/{owner}/{repo}/collaborators/{username}/permission``
answers 200 for any existing user. Someone who is not a collaborator, holds
no organization role and is on no team gets ``"permission": "none"`` and an
empty ``role_name`` on a private repository, and ``read`` on a public one,
because anyone can read it. Only a login that names no user is 404. The
emulator answered 404 for every user without access, so a client could not
tell "no access" from "no such user".
"""

import pytest

from tests.conftest import auth_headers
from tests.test_org_memberships_api import _make_user

API = "/api/v3"
NONE = {"admin": False, "maintain": False, "push": False, "triage": False, "pull": False}
READ = {"admin": False, "maintain": False, "push": False, "triage": False, "pull": True}

pytestmark = pytest.mark.asyncio


async def _repo(client, token, path, name, private):
    resp = await client.post(f"{API}/{path}", json={"name": name, "private": private}, headers=auth_headers(token))
    assert resp.status_code == 201, resp.text


async def _permission(client, repo, login, token):
    return await client.get(f"{API}/repos/{repo}/collaborators/{login}/permission", headers=auth_headers(token))


async def test_a_user_without_access_to_a_private_repository_has_none(client, test_user, test_token, db_session):
    await _make_user(db_session, "outsider")
    await _repo(client, test_token, "user/repos", "secret", True)

    resp = await _permission(client, "testuser/secret", "outsider", test_token)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert (data["permission"], data["role_name"]) == ("none", "")
    assert data["user"]["login"] == "outsider"
    assert data["user"]["permissions"] == NONE
    assert data["user"]["role_name"] == ""


async def test_a_user_without_access_to_a_public_repository_can_read(client, test_user, test_token, db_session):
    await _make_user(db_session, "outsider")
    await _repo(client, test_token, "user/repos", "open", False)

    resp = await _permission(client, "testuser/open", "outsider", test_token)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert (data["permission"], data["role_name"]) == ("read", "read")
    assert data["user"]["permissions"] == READ


async def test_a_login_that_names_no_user_is_not_found(client, test_user, test_token):
    await _repo(client, test_token, "user/repos", "secret", True)
    resp = await _permission(client, "testuser/secret", "nobody-at-all", test_token)
    assert resp.status_code == 404


async def test_an_org_member_whose_base_permission_grants_nothing(client, test_user, test_token, db_session):
    await _make_user(db_session, "member")
    assert (await client.post(f"{API}/orgs", json={"login": "none-org"}, headers=auth_headers(test_token))).status_code == 201
    joined = await client.put(f"{API}/orgs/none-org/memberships/member", json={"role": "member"},
                              headers=auth_headers(test_token))
    assert joined.status_code == 200
    await _repo(client, test_token, "orgs/none-org/repos", "hidden", True)
    await _repo(client, test_token, "orgs/none-org/repos", "shown", False)

    hidden = (await _permission(client, "none-org/hidden", "member", test_token)).json()
    assert (hidden["permission"], hidden["role_name"]) == ("none", "")
    shown = (await _permission(client, "none-org/shown", "member", test_token)).json()
    assert (shown["permission"], shown["role_name"]) == ("read", "read")


async def test_a_collaborator_still_reports_their_role(client, test_user, test_token, db_session):
    await _make_user(db_session, "helper")
    await _repo(client, test_token, "user/repos", "secret", True)
    added = await client.put(f"{API}/repos/testuser/secret/collaborators/helper", json={"permission": "triage"},
                             headers=auth_headers(test_token))
    assert added.status_code == 201

    data = (await _permission(client, "testuser/secret", "helper", test_token)).json()
    assert (data["permission"], data["role_name"]) == ("read", "triage")
    assert data["user"]["permissions"]["triage"] is True
