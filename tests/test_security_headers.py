"""Tests for request tracing and baseline HTTP security headers."""

import pytest


EXPECTED_SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "SAMEORIGIN",
    "referrer-policy": "same-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
}


def _assert_security_headers(response) -> None:
    for name, value in EXPECTED_SECURITY_HEADERS.items():
        assert response.headers[name] == value


def _assert_request_id_headers(response) -> None:
    request_id = response.headers["x-request-id"]
    assert request_id
    assert response.headers["x-github-request-id"] == request_id


@pytest.mark.asyncio
async def test_api_responses_include_security_and_request_id_headers(client):
    response = await client.get("/api/v3/emojis")
    assert response.status_code == 200
    _assert_security_headers(response)
    _assert_request_id_headers(response)


@pytest.mark.asyncio
async def test_admin_html_and_legacy_redirect_include_security_headers(client):
    redirect = await client.get("/admin/login", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "/ui/_admin/login"
    _assert_security_headers(redirect)

    response = await client.get("/ui/_admin/login")
    assert response.status_code == 200
    _assert_security_headers(response)
    _assert_request_id_headers(response)


@pytest.mark.asyncio
async def test_error_responses_include_security_and_request_id_headers(client):
    response = await client.get("/api/v3/does-not-exist")
    assert response.status_code == 404
    _assert_security_headers(response)
    _assert_request_id_headers(response)


@pytest.mark.asyncio
async def test_request_id_headers_preserve_incoming_request_id(client):
    response = await client.get(
        "/api/v3/emojis",
        headers={"X-Request-Id": "client-request-123"},
    )
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "client-request-123"
    assert response.headers["x-github-request-id"] == "client-request-123"


@pytest.mark.asyncio
async def test_legacy_redirect_preserves_path_and_query_without_moving_api(client):
    response = await client.get(
        "/admin/apps?state=active",
        follow_redirects=False,
    )
    assert response.status_code == 307
    assert response.headers["location"] == "/ui/_admin/apps?state=active"

    api_response = await client.get("/admin/api/does-not-exist")
    assert api_response.status_code == 404
    assert "location" not in api_response.headers
