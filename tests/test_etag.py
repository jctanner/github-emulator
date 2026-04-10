"""Tests for ETag middleware and conditional requests."""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


@pytest.mark.asyncio
async def test_etag_header_present(client, test_user, test_token):
    """Responses include an ETag header."""
    await client.post(
        f"{API}/user/repos", json={"name": "etag-repo"}, headers=auth_headers(test_token)
    )
    resp = await client.get(f"{API}/repos/testuser/etag-repo")
    assert resp.status_code == 200
    etag = resp.headers.get("etag")
    assert etag is not None


@pytest.mark.asyncio
async def test_if_none_match_304(client, test_user, test_token):
    """If-None-Match with matching ETag returns 304."""
    await client.post(
        f"{API}/user/repos", json={"name": "etag-304"}, headers=auth_headers(test_token)
    )
    resp = await client.get(f"{API}/repos/testuser/etag-304")
    assert resp.status_code == 200
    etag = resp.headers.get("etag")
    assert etag is not None

    # Send conditional request
    resp2 = await client.get(
        f"{API}/repos/testuser/etag-304",
        headers={"If-None-Match": etag},
    )
    assert resp2.status_code == 304


@pytest.mark.asyncio
async def test_if_none_match_mismatch(client, test_user, test_token):
    """If-None-Match with non-matching ETag returns 200."""
    await client.post(
        f"{API}/user/repos", json={"name": "etag-200"}, headers=auth_headers(test_token)
    )
    resp = await client.get(
        f"{API}/repos/testuser/etag-200",
        headers={"If-None-Match": '"non-matching-etag"'},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_etag_on_list_endpoint(client, test_user, test_token):
    """ETag is present on list endpoints."""
    await client.post(
        f"{API}/user/repos", json={"name": "etag-list"}, headers=auth_headers(test_token)
    )
    resp = await client.get(f"{API}/users/testuser/repos")
    assert resp.status_code == 200
    # ETag should be present
    etag = resp.headers.get("etag")
    assert etag is not None


@pytest.mark.asyncio
async def test_etag_consistency(client, test_user, test_token):
    """Same request returns same ETag."""
    await client.post(
        f"{API}/user/repos", json={"name": "etag-cons"}, headers=auth_headers(test_token)
    )
    resp1 = await client.get(f"{API}/repos/testuser/etag-cons")
    resp2 = await client.get(f"{API}/repos/testuser/etag-cons")
    assert resp1.headers.get("etag") == resp2.headers.get("etag")
