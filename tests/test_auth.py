"""Tests for authentication endpoints and token validation."""

import asyncio
import base64
import time

import pytest
from sqlalchemy import select

from app.models.token import PersonalAccessToken
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
async def test_pat_last_used_update_is_throttled(
    client, db_session, test_user, test_token
):
    """Repeated PAT requests do not turn every API read into a DB write."""
    first_response = await client.get(
        f"{API}/user", headers=auth_headers(test_token)
    )
    assert first_response.status_code == 200
    pat = (
        await db_session.execute(
            select(PersonalAccessToken).where(
                PersonalAccessToken.user_id == test_user.id
            )
        )
    ).scalar_one()
    await db_session.refresh(pat)
    first_used_at = pat.last_used_at
    assert first_used_at is not None

    second_response = await client.get(
        f"{API}/user", headers=auth_headers(test_token)
    )
    assert second_response.status_code == 200
    await db_session.refresh(pat)
    assert pat.last_used_at == first_used_at


@pytest.mark.asyncio
async def test_parallel_pat_requests_share_one_usage_update(
    client, db_session, test_user, test_token
):
    """A page's parallel PAT requests do not compete for SQLite writes."""
    responses = await asyncio.gather(
        *(
            client.get(f"{API}/user", headers=auth_headers(test_token))
            for _ in range(4)
        )
    )
    assert [response.status_code for response in responses] == [200] * 4
    pat = (
        await db_session.execute(
            select(PersonalAccessToken).where(
                PersonalAccessToken.user_id == test_user.id
            )
        )
    ).scalar_one()
    await db_session.refresh(pat)
    assert pat.last_used_at is not None


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
async def test_basic_auth_with_token(
    client, test_user, test_token, monkeypatch
):
    """Authorization: Basic <base64(username:token)> works."""
    def unexpected_password_check(*_args):
        raise AssertionError("a PAT must not be passed through bcrypt")

    monkeypatch.setattr(
        "app.services.auth_service.verify_password", unexpected_password_check
    )
    creds = base64.b64encode(f"testuser:{test_token}".encode()).decode()
    resp = await client.get(
        f"{API}/user",
        headers={"Authorization": f"Basic {creds}"},
    )
    assert resp.status_code == 200
    assert resp.json()["login"] == "testuser"


@pytest.mark.asyncio
async def test_parallel_basic_password_requests_share_one_bcrypt_check(
    client, test_user, monkeypatch
):
    """Parallel browser requests do not run serial bcrypt checks on the loop."""
    from app.services import auth_service

    auth_service._basic_password_cache.clear()
    auth_service._basic_password_locks.clear()
    calls = []

    def slow_verify(plain, _hashed):
        calls.append(plain)
        time.sleep(0.05)
        return plain == "password"

    monkeypatch.setattr(auth_service, "verify_password", slow_verify)
    creds = base64.b64encode(b"testuser:password").decode()
    headers = {"Authorization": f"Basic {creds}"}
    responses = await asyncio.gather(
        *(client.get(f"{API}/user", headers=headers) for _ in range(4))
    )

    assert [response.status_code for response in responses] == [200] * 4
    assert calls == ["password"]


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
