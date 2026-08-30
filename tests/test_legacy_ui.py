"""Contract tests for the temporary server-rendered UI mirror."""

import pytest


@pytest.mark.asyncio
async def test_legacy_landing_rewrites_internal_links(client):
    response = await client.get("/ui-legacy/")

    assert response.status_code == 200
    assert 'href="/ui-legacy/"' in response.text
    assert 'href="/ui-legacy/login"' in response.text
    assert 'href="/ui-legacy/_admin/"' in response.text
    assert 'href="/ui-legacy/static/css/web.css"' in response.text


@pytest.mark.asyncio
async def test_legacy_login_keeps_redirect_and_cookie_in_legacy_namespace(
    client, admin_user, db_session
):
    from app.services.auth_service import hash_password

    admin_user.hashed_password = hash_password("admin")
    await db_session.commit()

    response = await client.post(
        "/ui-legacy/login",
        data={"username": "admin", "password": "admin"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/ui-legacy/"
    assert "Path=/ui-legacy" in response.headers["set-cookie"]

    landing = await client.get("/ui-legacy/")
    assert landing.status_code == 200
    assert "admin" in landing.text


@pytest.mark.asyncio
async def test_legacy_admin_and_static_assets_are_available(client):
    login = await client.get("/ui-legacy/_admin/login")
    stylesheet = await client.get("/ui-legacy/_admin/static/css/admin.css")

    assert login.status_code == 200
    assert 'action="/ui-legacy/_admin/login"' in login.text
    assert stylesheet.status_code == 200
    assert "text/css" in stylesheet.headers["content-type"]


@pytest.mark.asyncio
async def test_canonical_ui_uses_api_client_frontend_after_cutover(client):
    response = await client.get("/ui/")

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text
    assert 'href="/ui/assets/' in response.text
