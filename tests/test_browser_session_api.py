"""Browser-session and CSRF contract tests."""

import base64

import pytest
from sqlalchemy import event

from app.services.auth_service import hash_password

UI_API = "/api/_ui"


@pytest.mark.asyncio
async def test_browser_session_login_current_user_and_logout(
    client, test_user, db_session
):
    test_user.hashed_password = hash_password("password")
    await db_session.commit()

    login = await client.post(
        f"{UI_API}/session",
        json={"username": "testuser", "password": "password"},
    )
    assert login.status_code == 200
    payload = login.json()
    assert payload["user"]["login"] == "testuser"
    assert payload["csrf_token"]
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "Path=/" in login.headers["set-cookie"]

    current = await client.get(f"{UI_API}/session")
    assert current.status_code == 200
    assert current.json()["user"]["login"] == "testuser"

    # Browsers can retain origin-wide Basic credentials. A valid same-origin
    # UI session must avoid an unnecessary bcrypt check for every API fetch.
    stale_basic = base64.b64encode(b"testuser:wrong-password").decode()
    same_origin = await client.get(
        f"{UI_API}/session",
        headers={
            "Authorization": f"Basic {stale_basic}",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert same_origin.status_code == 200
    assert same_origin.json()["user"]["login"] == "testuser"

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
        f"{UI_API}/session",
        headers={"X-CSRF-Token": payload["csrf_token"]},
    )
    assert logout.status_code == 204

    missing = await client.get(f"{UI_API}/session")
    assert missing.status_code == 401


@pytest.mark.asyncio
async def test_users_created_through_the_admin_api_can_sign_in(client):
    created = await client.post(
        "/api/v3/admin/users",
        json={"login": "seeded", "password": "seeded-pass", "email": "seeded@example.invalid"},
    )
    assert created.status_code == 201
    login = await client.post(
        f"{UI_API}/session",
        json={"username": "seeded", "password": "seeded-pass"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["login"] == "seeded"


@pytest.mark.asyncio
async def test_signed_out_page_requests_get_no_basic_challenge(client):
    # A challenge on the web UI's own fetch would open the browser's native
    # sign-in dialog over the login page.
    page_fetch = await client.get(
        f"{UI_API}/session",
        headers={"Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin"},
    )
    assert page_fetch.status_code == 401
    assert "www-authenticate" not in page_fetch.headers

    # API clients and direct browser navigation still get the challenge.
    api_client = await client.get(f"{UI_API}/session")
    assert api_client.headers["www-authenticate"] == 'Basic realm="GitHub Emulator"'
    navigation = await client.get(
        "/api/v3/user", headers={"Sec-Fetch-Mode": "navigate"}
    )
    assert navigation.status_code == 401
    assert navigation.headers["www-authenticate"] == 'Basic realm="GitHub Emulator"'


@pytest.mark.asyncio
async def test_browser_session_rejects_invalid_credentials(client):
    response = await client.post(
        f"{UI_API}/session",
        json={"username": "missing", "password": "wrong"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_browser_session_lookup_does_not_load_user_relationships(
    client, test_user, db_session, db_engine
):
    test_user.hashed_password = hash_password("password")
    await db_session.commit()
    login = await client.post(
        f"{UI_API}/session",
        json={"username": "testuser", "password": "password"},
    )
    assert login.status_code == 200

    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(db_engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        response = await client.get(f"{UI_API}/session")
    finally:
        event.remove(db_engine.sync_engine, "before_cursor_execute", record_statement)

    assert response.status_code == 200
    # The user row, and one count of their public repositories.
    assert len(statements) == 2, statements
    assert "FROM repositories" in statements[1]


@pytest.mark.asyncio
async def test_browser_session_counts_the_users_public_repositories(
    client, test_user, test_token, db_session
):
    from tests.conftest import API, auth_headers

    test_user.hashed_password = hash_password("session-pw")
    await db_session.commit()
    for name, private in (("open-one", False), ("open-two", False), ("closed", True)):
        created = await client.post(
            f"{API}/user/repos", json={"name": name, "private": private}, headers=auth_headers(test_token)
        )
        assert created.status_code == 201, created.text

    login = await client.post(f"{UI_API}/session", json={"username": "testuser", "password": "session-pw"})
    assert login.status_code == 200, login.text
    assert login.json()["user"]["public_repos"] == 2
    current = await client.get(f"{UI_API}/session")
    assert current.json()["user"]["public_repos"] == 2
