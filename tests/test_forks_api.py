"""Tests for the Fork REST API endpoints."""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


@pytest.mark.asyncio
async def test_create_fork(client, test_user, test_token, admin_user, admin_token):
    """POST /repos/{owner}/{repo}/forks creates a fork."""
    # Create original repo
    await client.post(
        f"{API}/user/repos",
        json={"name": "fork-source", "auto_init": True},
        headers=auth_headers(test_token),
    )
    # Fork it as admin
    resp = await client.post(
        f"{API}/repos/testuser/fork-source/forks",
        json={},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["fork"] is True
    assert data["full_name"] == "admin/fork-source"


@pytest.mark.asyncio
async def test_list_forks(client, test_user, test_token, admin_user, admin_token):
    """GET /repos/{owner}/{repo}/forks lists forks."""
    await client.post(
        f"{API}/user/repos",
        json={"name": "fork-list"},
        headers=auth_headers(test_token),
    )
    await client.post(
        f"{API}/repos/testuser/fork-list/forks",
        json={},
        headers=auth_headers(admin_token),
    )
    resp = await client.get(f"{API}/repos/testuser/fork-list/forks")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_fork_preserves_description(client, test_user, test_token, admin_user, admin_token):
    """Forked repo inherits parent description."""
    await client.post(
        f"{API}/user/repos",
        json={"name": "fork-desc", "description": "Original description"},
        headers=auth_headers(test_token),
    )
    resp = await client.post(
        f"{API}/repos/testuser/fork-desc/forks",
        json={},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 202
    assert resp.json()["description"] == "Original description"


@pytest.mark.asyncio
async def test_duplicate_fork_returns_the_existing_fork(client, test_user, test_token, admin_user, admin_token):
    """Asking again for an existing fork returns it (202), as GitHub does;
    a name taken by a repository that is not that fork is refused (422)."""
    await client.post(
        f"{API}/user/repos",
        json={"name": "fork-dupe"},
        headers=auth_headers(test_token),
    )
    await client.post(
        f"{API}/repos/testuser/fork-dupe/forks",
        json={},
        headers=auth_headers(admin_token),
    )
    resp = await client.post(
        f"{API}/repos/testuser/fork-dupe/forks",
        json={},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 202
    assert resp.json()["full_name"] == "admin/fork-dupe"
    await client.post(f"{API}/user/repos", json={"name": "taken"}, headers=auth_headers(admin_token))
    clash = await client.post(
        f"{API}/repos/testuser/fork-dupe/forks",
        json={"name": "taken"},
        headers=auth_headers(admin_token),
    )
    assert clash.status_code == 422


@pytest.mark.asyncio
async def test_fork_with_custom_name(client, test_user, test_token, admin_user, admin_token):
    """Fork can have a custom name."""
    await client.post(
        f"{API}/user/repos",
        json={"name": "fork-named"},
        headers=auth_headers(test_token),
    )
    resp = await client.post(
        f"{API}/repos/testuser/fork-named/forks",
        json={"name": "my-custom-fork"},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 202
    assert resp.json()["name"] == "my-custom-fork"


@pytest.mark.asyncio
async def test_fork_requires_auth(client, test_user, test_token):
    """Creating a fork requires authentication."""
    await client.post(
        f"{API}/user/repos",
        json={"name": "fork-auth"},
        headers=auth_headers(test_token),
    )
    resp = await client.post(
        f"{API}/repos/testuser/fork-auth/forks",
        json={},
    )
    assert resp.status_code == 401
