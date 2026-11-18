"""Naming a label that does not exist yet, on issue create or update, creates it.

GitHub, for a caller with push access, creates each missing label with the
default color `ededed` and applies it, as `POST /issues/{n}/labels` already
does here. The emulator dropped unknown names on `POST /issues` and
`PATCH /issues/{n}`, so the issue came back with no labels and no label was
created.

Found by Fullsend's behaviour tests, which open issues with labels that the
repository has not defined yet.
"""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


async def _repo(client, token, name):
    resp = await client.post(
        f"{API}/user/repos", json={"name": name}, headers=auth_headers(token)
    )
    assert resp.status_code == 201, resp.text


async def _repo_labels(client, token, repo):
    resp = await client.get(
        f"{API}/repos/testuser/{repo}/labels", headers=auth_headers(token)
    )
    assert resp.status_code == 200, resp.text
    return {label["name"]: label for label in resp.json()}


@pytest.mark.asyncio
async def test_issue_create_creates_and_applies_unknown_labels(client, test_user, test_token):
    await _repo(client, test_token, "new-labels")
    await client.post(
        f"{API}/repos/testuser/new-labels/labels",
        json={"name": "bug", "color": "d73a4a"},
        headers=auth_headers(test_token),
    )

    resp = await client.post(
        f"{API}/repos/testuser/new-labels/issues",
        json={"title": "t", "labels": ["bug", "new-label", "new-label"]},
        headers=auth_headers(test_token),
    )

    assert resp.status_code == 201, resp.text
    assert sorted(label["name"] for label in resp.json()["labels"]) == ["bug", "new-label"]
    labels = await _repo_labels(client, test_token, "new-labels")
    assert labels["new-label"]["color"] == "ededed"
    assert labels["bug"]["color"] == "d73a4a"


@pytest.mark.asyncio
async def test_issue_create_accepts_label_objects(client, test_user, test_token):
    """GitHub's `labels` may hold objects with a `name` as well as strings."""
    await _repo(client, test_token, "label-objects")

    resp = await client.post(
        f"{API}/repos/testuser/label-objects/issues",
        json={"title": "t", "labels": [{"name": "triage"}]},
        headers=auth_headers(test_token),
    )

    assert resp.status_code == 201, resp.text
    assert [label["name"] for label in resp.json()["labels"]] == ["triage"]


@pytest.mark.asyncio
async def test_issue_update_creates_and_applies_unknown_labels(client, test_user, test_token):
    await _repo(client, test_token, "update-labels")
    created = await client.post(
        f"{API}/repos/testuser/update-labels/issues",
        json={"title": "t"},
        headers=auth_headers(test_token),
    )
    number = created.json()["number"]

    resp = await client.patch(
        f"{API}/repos/testuser/update-labels/issues/{number}",
        json={"labels": ["later-label"]},
        headers=auth_headers(test_token),
    )

    assert resp.status_code == 200, resp.text
    assert [label["name"] for label in resp.json()["labels"]] == ["later-label"]
    labels = await _repo_labels(client, test_token, "update-labels")
    assert labels["later-label"]["color"] == "ededed"

    events = await client.get(
        f"{API}/repos/testuser/update-labels/issues/{number}/events",
        headers=auth_headers(test_token),
    )
    assert [
        (event["event"], event["label"]["name"]) for event in events.json()
        if event["event"] == "labeled"
    ] == [("labeled", "later-label")]
