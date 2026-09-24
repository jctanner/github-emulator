"""Deleting a ref that is not there must fail, not report success.

`git update-ref -d` exits 0 for a missing ref, so the handler returned 204 and
a caller could not tell "I removed it" from "it was already gone". GitHub
answers 422 "Reference does not exist".

Found by a cleanup routine that deletes the branches behind agent pull
requests: run twice, it reported deleting seventeen branches both times.
"""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


async def _repo(client, token, name):
    resp = await client.post(
        f"{API}/user/repos", json={"name": name, "auto_init": True},
        headers=auth_headers(token),
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_deleting_a_missing_ref_is_refused(client, test_user, test_token):
    await _repo(client, test_token, "dr-missing")
    resp = await client.delete(
        f"{API}/repos/testuser/dr-missing/git/refs/heads/no-such-branch",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 422, resp.text
    assert "does not exist" in str(resp.json()).lower()


@pytest.mark.asyncio
async def test_deleting_an_existing_ref_still_works(client, test_user, test_token):
    """The success path is unchanged: 204, and the ref is gone afterwards."""
    repo = await _repo(client, test_token, "dr-present")
    main = await client.get(
        f"{API}/repos/testuser/dr-present/git/ref/heads/{repo['default_branch']}",
        headers=auth_headers(test_token),
    )
    assert main.status_code == 200, main.text
    sha = main.json()["object"]["sha"]

    created = await client.post(
        f"{API}/repos/testuser/dr-present/git/refs",
        json={"ref": "refs/heads/scratch", "sha": sha},
        headers=auth_headers(test_token),
    )
    assert created.status_code == 201, created.text

    gone = await client.delete(
        f"{API}/repos/testuser/dr-present/git/refs/heads/scratch",
        headers=auth_headers(test_token),
    )
    assert gone.status_code == 204, gone.text

    check = await client.get(
        f"{API}/repos/testuser/dr-present/git/ref/heads/scratch",
        headers=auth_headers(test_token),
    )
    assert check.status_code == 404


@pytest.mark.asyncio
async def test_deleting_twice_reports_the_second_as_missing(client, test_user, test_token):
    """The case the cleanup routine actually hit."""
    repo = await _repo(client, test_token, "dr-twice")
    sha = (await client.get(
        f"{API}/repos/testuser/dr-twice/git/ref/heads/{repo['default_branch']}",
        headers=auth_headers(test_token),
    )).json()["object"]["sha"]
    await client.post(
        f"{API}/repos/testuser/dr-twice/git/refs",
        json={"ref": "refs/heads/once", "sha": sha},
        headers=auth_headers(test_token),
    )
    first = await client.delete(
        f"{API}/repos/testuser/dr-twice/git/refs/heads/once",
        headers=auth_headers(test_token),
    )
    second = await client.delete(
        f"{API}/repos/testuser/dr-twice/git/refs/heads/once",
        headers=auth_headers(test_token),
    )
    assert first.status_code == 204
    assert second.status_code == 422, "the second delete reported success"
