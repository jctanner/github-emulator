"""REST responses the conformance recheck compared against github.com.

- A pull request carries the labels of its issue.
- A label whose name holds "/" is reachable through every per-name route.
- PATCH issue sets state_reason on a close or reopen the request leaves unexplained.
- GET /repos/{owner}/{repo}/hash-algorithm answers.
"""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"
REPO = f"{API}/repos/testuser/gaps"


@pytest.fixture
async def repo(client, test_user, test_token):
    resp = await client.post(f"{API}/user/repos", json={"name": "gaps"}, headers=auth_headers(test_token))
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_a_pull_request_carries_its_issue_labels(client, test_token, repo):
    h = auth_headers(test_token)
    resp = await client.post(f"{REPO}/pulls", json={"title": "PR", "head": "feature", "base": "main"}, headers=h)
    assert resp.status_code == 201, resp.text
    assert resp.json()["labels"] == []

    resp = await client.post(f"{REPO}/issues/1/labels", json={"labels": ["ok-to-test"]}, headers=h)
    assert resp.status_code == 200, resp.text

    single = await client.get(f"{REPO}/pulls/1", headers=h)
    assert single.status_code == 200, single.text
    assert [label["name"] for label in single.json()["labels"]] == ["ok-to-test"]

    listed = await client.get(f"{REPO}/pulls", headers=h)
    assert [label["name"] for label in listed.json()[0]["labels"]] == ["ok-to-test"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path_name", ["risk/low", "risk%2Flow"])
async def test_a_label_name_with_a_slash_is_reachable(client, test_token, repo, path_name):
    h = auth_headers(test_token)
    resp = await client.post(f"{REPO}/labels", json={"name": "risk/low", "color": "00ff00"}, headers=h)
    assert resp.status_code == 201, resp.text

    got = await client.get(f"{REPO}/labels/{path_name}", headers=h)
    assert got.status_code == 200, got.text
    assert got.json()["name"] == "risk/low"

    patched = await client.patch(f"{REPO}/labels/{path_name}", json={"color": "ff0000"}, headers=h)
    assert patched.status_code == 200, patched.text
    assert patched.json()["color"] == "ff0000"

    resp = await client.post(f"{REPO}/issues", json={"title": "I", "labels": ["risk/low"]}, headers=h)
    assert resp.status_code == 201, resp.text
    removed = await client.delete(f"{REPO}/issues/1/labels/{path_name}", headers=h)
    assert removed.status_code == 200, removed.text
    assert removed.json() == []

    deleted = await client.delete(f"{REPO}/labels/{path_name}", headers=h)
    assert deleted.status_code == 204, deleted.text
    gone = await client.get(f"{REPO}/labels/{path_name}", headers=h)
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_patch_issue_resets_the_state_reason(client, test_token, repo):
    h = auth_headers(test_token)
    resp = await client.post(f"{REPO}/issues", json={"title": "I"}, headers=h)
    assert resp.status_code == 201, resp.text
    url = f"{REPO}/issues/1"

    async def patch(body):
        r = await client.patch(url, json=body, headers=h)
        assert r.status_code == 200, r.text
        return r.json()["state_reason"]

    assert await patch({"state": "closed", "state_reason": "not_planned"}) == "not_planned"
    assert await patch({"state": "open"}) == "reopened"
    assert await patch({"state": "closed"}) == "completed"
    assert await patch({"title": "renamed"}) == "completed"


@pytest.mark.asyncio
async def test_hash_algorithm(client, test_token, repo):
    resp = await client.get(f"{REPO}/hash-algorithm", headers=auth_headers(test_token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"hash_algorithm": "sha1"}

    missing = await client.get(f"{API}/repos/testuser/nope/hash-algorithm", headers=auth_headers(test_token))
    assert missing.status_code == 404
