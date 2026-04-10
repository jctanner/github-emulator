"""Tests for authentication endpoints and token validation."""

import base64

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


@pytest.mark.asyncio
async def test_unauthenticated_get_user(client):
    """GET /user without auth returns 401."""
    resp = await client.get(f"{API}/user")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_authenticated_get_user(client, test_user, test_token):
    """GET /user with valid token returns user profile."""
    resp = await client.get(f"{API}/user", headers=auth_headers(test_token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["login"] == "testuser"
    assert data["type"] == "User"


@pytest.mark.asyncio
async def test_bearer_auth(client, test_user, test_token):
    """Authorization: Bearer <token> works."""
    resp = await client.get(
        f"{API}/user",
        headers={"Authorization": f"Bearer {test_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["login"] == "testuser"


@pytest.mark.asyncio
async def test_basic_auth_with_token(client, test_user, test_token):
    """Authorization: Basic <base64(username:token)> works."""
    creds = base64.b64encode(f"testuser:{test_token}".encode()).decode()
    resp = await client.get(
        f"{API}/user",
        headers={"Authorization": f"Basic {creds}"},
    )
    assert resp.status_code == 200
    assert resp.json()["login"] == "testuser"


@pytest.mark.asyncio
async def test_invalid_token(client):
    """Invalid token returns 401."""
    resp = await client.get(
        f"{API}/user",
        headers={"Authorization": "token invalid_token_here"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_public_user(client, test_user):
    """GET /users/{username} works without auth."""
    resp = await client.get(f"{API}/users/testuser")
    assert resp.status_code == 200
    data = resp.json()
    assert data["login"] == "testuser"
    assert "id" in data
    assert "node_id" in data


@pytest.mark.asyncio
async def test_get_nonexistent_user(client):
    """GET /users/{username} returns 404 for missing user."""
    resp = await client.get(f"{API}/users/nosuchuser")
    assert resp.status_code == 404
