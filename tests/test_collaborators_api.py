"""Tests for the Collaborator REST API endpoints."""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


@pytest.mark.asyncio
async def test_list_collaborators(client, test_user, test_token):
    """GET /repos/{owner}/{repo}/collaborators lists collaborators."""
    await client.post(
        f"{API}/user/repos", json={"name": "collab-repo"}, headers=auth_headers(test_token)
    )
    resp = await client.get(
        f"{API}/repos/testuser/collab-repo/collaborators",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    # Owner is always included
    assert len(data) >= 1
    assert any(c["login"] == "testuser" for c in data)


@pytest.mark.asyncio
async def test_add_collaborator(client, test_user, test_token, admin_user, admin_token):
    """PUT /repos/{owner}/{repo}/collaborators/{username} adds a collaborator."""
    await client.post(
        f"{API}/user/repos", json={"name": "collab-add"}, headers=auth_headers(test_token)
    )
    resp = await client.put(
        f"{API}/repos/testuser/collab-add/collaborators/admin",
        json={"permission": "push"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_check_collaborator_is_owner(client, test_user, test_token):
    """GET /repos/{owner}/{repo}/collaborators/{username} returns 204 for owner."""
    await client.post(
        f"{API}/user/repos", json={"name": "collab-chk"}, headers=auth_headers(test_token)
    )
    resp = await client.get(
        f"{API}/repos/testuser/collab-chk/collaborators/testuser",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_check_collaborator_not_found(client, test_user, test_token):
    """GET /repos/{owner}/{repo}/collaborators/{username} returns 404 for non-collaborator."""
    await client.post(
        f"{API}/user/repos", json={"name": "collab-404"}, headers=auth_headers(test_token)
    )
    resp = await client.get(
        f"{API}/repos/testuser/collab-404/collaborators/nobody",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_remove_collaborator(client, test_user, test_token, admin_user, admin_token):
    """DELETE /repos/{owner}/{repo}/collaborators/{username} removes collaborator."""
    await client.post(
        f"{API}/user/repos", json={"name": "collab-rm"}, headers=auth_headers(test_token)
    )
    await client.put(
        f"{API}/repos/testuser/collab-rm/collaborators/admin",
        json={"permission": "push"},
        headers=auth_headers(test_token),
    )
    resp = await client.delete(
        f"{API}/repos/testuser/collab-rm/collaborators/admin",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_get_collaborator_permission(client, test_user, test_token, admin_user, admin_token):
    """GET /repos/{owner}/{repo}/collaborators/{username}/permission returns permission."""
    await client.post(
        f"{API}/user/repos", json={"name": "collab-perm"}, headers=auth_headers(test_token)
    )
    await client.put(
        f"{API}/repos/testuser/collab-perm/collaborators/admin",
        json={"permission": "push"},
        headers=auth_headers(test_token),
    )
    resp = await client.get(
        f"{API}/repos/testuser/collab-perm/collaborators/admin/permission",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["permission"] == "push"
    assert "user" in data


@pytest.mark.asyncio
async def test_owner_permission_is_admin(client, test_user, test_token):
    """Owner always has admin permission."""
    await client.post(
        f"{API}/user/repos", json={"name": "collab-own"}, headers=auth_headers(test_token)
    )
    resp = await client.get(
        f"{API}/repos/testuser/collab-own/collaborators/testuser/permission",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    assert resp.json()["permission"] == "admin"


@pytest.mark.asyncio
async def test_add_nonexistent_user_as_collaborator(client, test_user, test_token):
    """PUT collaborator for non-existent user returns 404."""
    await client.post(
        f"{API}/user/repos", json={"name": "collab-nf"}, headers=auth_headers(test_token)
    )
    resp = await client.put(
        f"{API}/repos/testuser/collab-nf/collaborators/nonexistent_user_xyz",
        json={"permission": "push"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 404
