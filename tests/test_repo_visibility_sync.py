"""PATCH /repos/{owner}/{repo} keeps ``private`` and ``visibility`` in step.

GitHub treats them as one setting: ``{"private": false}`` makes the
repository public and ``{"visibility": "public"}`` makes ``private`` false.
The emulator set only the field it was given, so a repository could read
``private: false`` with visibility ``private``, and a client that reads
``visibility`` saw the wrong one.
"""

import pytest

from tests.conftest import API, auth_headers
from tests.test_private_repository_access import outsider_token  # noqa: F401 (fixture)


async def _repo(client, token, private):
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "flip", "private": private, "auto_init": True},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["full_name"]


async def _patch(client, token, full_name, body):
    resp = await client.patch(f"{API}/repos/{full_name}", json=body, headers=auth_headers(token))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    stored = (await client.get(f"{API}/repos/{full_name}", headers=auth_headers(token))).json()
    assert (stored["private"], stored["visibility"]) == (data["private"], data["visibility"])
    return data["private"], data["visibility"]


@pytest.mark.asyncio
async def test_private_false_makes_the_repository_public(client, test_token):
    full_name = await _repo(client, test_token, private=True)
    assert await _patch(client, test_token, full_name, {"private": False}) == (False, "public")


@pytest.mark.asyncio
async def test_private_true_makes_the_repository_private(client, test_token):
    full_name = await _repo(client, test_token, private=False)
    assert await _patch(client, test_token, full_name, {"private": True}) == (True, "private")


@pytest.mark.asyncio
async def test_visibility_sets_private(client, test_token):
    full_name = await _repo(client, test_token, private=True)
    assert await _patch(client, test_token, full_name, {"visibility": "public"}) == (False, "public")
    assert await _patch(client, test_token, full_name, {"visibility": "private"}) == (True, "private")


@pytest.mark.asyncio
async def test_a_repository_made_public_is_readable_by_others(
    client, test_token, outsider_token,  # noqa: F811
):
    full_name = await _repo(client, test_token, private=True)
    assert (await client.get(f"{API}/repos/{full_name}", headers=auth_headers(outsider_token))).status_code == 404
    await _patch(client, test_token, full_name, {"private": False})
    resp = await client.get(f"{API}/repos/{full_name}", headers=auth_headers(outsider_token))
    assert resp.status_code == 200, resp.text
