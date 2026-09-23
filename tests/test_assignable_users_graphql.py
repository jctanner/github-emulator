"""Repository.assignableUsers, the field that hands work to a human.

Fullsend's post-code script asks for this to assign a finished pull request
to a maintainer. The field took no arguments and returned an empty
connection, so it failed twice over: a client paginating it -- the normal way
to read a connection -- got `Unknown argument 'first'`, and a client that did
not would have got an empty list and assigned no one, more quietly.

Because the script treats assignment as non-fatal, the observable result was
a green run with a pull request nobody was assigned to. The run reported
success and the handoff did not happen.
"""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"
GRAPHQL = "/api/graphql"


async def _repo(client, token, name):
    resp = await client.post(
        f"{API}/user/repos", json={"name": name}, headers=auth_headers(token)
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _gql(client, token, query, variables=None):
    resp = await client.post(
        GRAPHQL,
        json={"query": query, "variables": variables or {}},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# The shape Fullsend sends: a paginated connection, not a bare field.
_QUERY = """
query($owner: String!, $name: String!, $first: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    assignableUsers(first: $first, after: $after) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes { login }
    }
  }
}
"""


@pytest.mark.asyncio
async def test_assignable_users_accepts_pagination(client, test_user, test_token):
    """`first` and `after` are accepted; previously this was a query error."""
    await _repo(client, test_token, "au-args")
    data = await _gql(
        client, test_token, _QUERY,
        {"owner": "testuser", "name": "au-args", "first": 30},
    )
    assert "errors" not in data, data["errors"]
    conn = data["data"]["repository"]["assignableUsers"]
    assert "hasNextPage" in conn["pageInfo"]


@pytest.mark.asyncio
async def test_the_owner_is_assignable(client, test_user, test_token):
    """An owner is always assignable, and need not be in the collaborators table.

    Returning an empty list here is what left the pull request unassigned, so
    "it answers without erroring" is not enough: it has to name someone.
    """
    await _repo(client, test_token, "au-owner")
    data = await _gql(
        client, test_token, _QUERY,
        {"owner": "testuser", "name": "au-owner", "first": 30},
    )
    conn = data["data"]["repository"]["assignableUsers"]
    logins = [n["login"] for n in conn["nodes"]]
    assert "testuser" in logins, f"the owner is not assignable; got {logins}"
    assert conn["totalCount"] >= 1


@pytest.mark.asyncio
async def test_collaborators_are_assignable(client, test_user, test_token):
    """A collaborator added through the REST API shows up here."""
    await _repo(client, test_token, "au-collab")
    await client.post(
        f"{API}/user", json={}, headers=auth_headers(test_token)
    )
    # Create a second user to add as a collaborator.
    reg = await client.post(
        f"{API}/admin/users",
        json={"login": "mate", "email": "mate@example.com", "password": "pw"},
        headers=auth_headers(test_token),
    )
    if reg.status_code not in (200, 201):
        pytest.skip(f"cannot create a second user here: {reg.status_code}")

    added = await client.put(
        f"{API}/repos/testuser/au-collab/collaborators/mate",
        json={"permission": "push"},
        headers=auth_headers(test_token),
    )
    assert added.status_code in (201, 204), added.text

    data = await _gql(
        client, test_token, _QUERY,
        {"owner": "testuser", "name": "au-collab", "first": 30},
    )
    logins = [n["login"] for n in data["data"]["repository"]["assignableUsers"]["nodes"]]
    assert "mate" in logins, f"collaborator missing from assignable users: {logins}"


@pytest.mark.asyncio
async def test_pagination_actually_pages(client, test_user, test_token):
    """`first: 1` returns one node, not everything."""
    await _repo(client, test_token, "au-page")
    data = await _gql(
        client, test_token, _QUERY,
        {"owner": "testuser", "name": "au-page", "first": 1},
    )
    conn = data["data"]["repository"]["assignableUsers"]
    assert len(conn["nodes"]) <= 1
