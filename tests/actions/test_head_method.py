"""HEAD must behave as GET without a body.

A client reading a response header without wanting the body uses HEAD; the
Fullsend CLI does exactly that against /user to read X-OAuth-Scopes before it
will write to a repository. FastAPI registers only the methods a route
declares, so every GET endpoint answered 405 until HeadMethodMiddleware.
"""

import pytest

from tests.conftest import API, auth_headers


@pytest.mark.asyncio
async def test_head_on_a_get_route_matches_the_get_status(client, test_token):
    get = await client.get(f"{API}/user", headers=auth_headers(test_token))
    head = await client.head(f"{API}/user", headers=auth_headers(test_token))
    assert get.status_code == 200
    assert head.status_code == get.status_code, "HEAD must not 405 on a GET resource"


@pytest.mark.asyncio
async def test_head_returns_no_body(client, test_token):
    head = await client.head(f"{API}/user", headers=auth_headers(test_token))
    assert head.content == b""


@pytest.mark.asyncio
async def test_head_keeps_the_headers_a_get_would_carry(client, test_token):
    get = await client.get(f"{API}/user", headers=auth_headers(test_token))
    head = await client.head(f"{API}/user", headers=auth_headers(test_token))
    # The point of HEAD is the headers; content-type in particular tells the
    # caller what the body would have been.
    assert head.headers.get("content-type") == get.headers.get("content-type")


@pytest.mark.asyncio
async def test_head_on_an_unauthenticated_route_still_refuses(client):
    head = await client.head(f"{API}/user")
    assert head.status_code in (401, 403), "HEAD must not bypass authentication"


@pytest.mark.asyncio
async def test_head_on_a_missing_route_is_still_not_found(client, test_token):
    head = await client.head(f"{API}/no-such-endpoint", headers=auth_headers(test_token))
    assert head.status_code == 404
