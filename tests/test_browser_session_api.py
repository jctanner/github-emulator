"""Browser-session and CSRF contract tests."""

import pytest

from app.services.auth_service import hash_password


@pytest.mark.asyncio
async def test_browser_session_login_current_user_and_logout(
    client, test_user, db_session
):
    test_user.hashed_password = hash_password("password")
    await db_session.commit()

    login = await client.post(
        "/api/v3/session",
        json={"username": "testuser", "password": "password"},
    )
    assert login.status_code == 200
    payload = login.json()
    assert payload["user"]["login"] == "testuser"
    assert payload["csrf_token"]
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "Path=/" in login.headers["set-cookie"]

    current = await client.get("/api/v3/session")
    assert current.status_code == 200
    assert current.json()["user"]["login"] == "testuser"

    rejected = await client.patch(
        "/api/v3/user",
        json={"bio": "blocked without CSRF"},
    )
    assert rejected.status_code == 403

    updated = await client.patch(
        "/api/v3/user",
        json={"bio": "allowed"},
        headers={"X-CSRF-Token": payload["csrf_token"]},
    )
    assert updated.status_code == 200
    assert updated.json()["bio"] == "allowed"

    logout = await client.delete(
        "/api/v3/session",
        headers={"X-CSRF-Token": payload["csrf_token"]},
    )
    assert logout.status_code == 204

    missing = await client.get("/api/v3/session")
    assert missing.status_code == 401


@pytest.mark.asyncio
async def test_browser_session_rejects_invalid_credentials(client):
    response = await client.post(
        "/api/v3/session",
        json={"username": "missing", "password": "wrong"},
    )
    assert response.status_code == 401
