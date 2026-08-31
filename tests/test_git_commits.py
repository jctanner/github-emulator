"""Tests for Git Data commit creation."""

import pytest

from tests.conftest import API, auth_headers


async def _head_and_tree(client, owner, repo, headers):
    ref = await client.get(
        f"{API}/repos/{owner}/{repo}/git/ref/heads/main", headers=headers
    )
    head = ref.json()["object"]["sha"]
    commit = await client.get(
        f"{API}/repos/{owner}/{repo}/git/commits/{head}", headers=headers
    )
    return head, commit.json()["tree"]["sha"]


@pytest.mark.asyncio
async def test_create_commit_defaults_identity_to_authenticated_user(
    client, test_user, test_token, test_repo_with_init
):
    owner, repo, _ = test_repo_with_init
    headers = auth_headers(test_token)
    head, tree = await _head_and_tree(client, owner, repo, headers)

    response = await client.post(
        f"{API}/repos/{owner}/{repo}/git/commits",
        json={"message": "Default identity", "tree": tree, "parents": [head]},
        headers=headers,
    )

    assert response.status_code == 201
    created = await client.get(
        f"{API}/repos/{owner}/{repo}/git/commits/{response.json()['sha']}",
        headers=headers,
    )
    assert created.json()["author"]["name"] == test_user.name
    assert created.json()["author"]["email"] == test_user.email
    assert created.json()["committer"]["name"] == test_user.name


@pytest.mark.asyncio
async def test_create_commit_honors_explicit_author_and_committer(
    client, test_token, test_repo_with_init
):
    owner, repo, _ = test_repo_with_init
    headers = auth_headers(test_token)
    head, tree = await _head_and_tree(client, owner, repo, headers)

    response = await client.post(
        f"{API}/repos/{owner}/{repo}/git/commits",
        json={
            "message": "Explicit identity",
            "tree": tree,
            "parents": [head],
            "author": {
                "name": "API Author",
                "email": "author@example.test",
                "date": "2026-08-20T12:00:00Z",
            },
            "committer": {
                "name": "API Committer",
                "email": "committer@example.test",
                "date": "2026-08-20T12:01:00Z",
            },
        },
        headers=headers,
    )

    assert response.status_code == 201
    created = await client.get(
        f"{API}/repos/{owner}/{repo}/git/commits/{response.json()['sha']}",
        headers=headers,
    )
    assert created.json()["author"]["name"] == "API Author"
    assert created.json()["author"]["email"] == "author@example.test"
    assert created.json()["committer"]["name"] == "API Committer"
    assert created.json()["committer"]["email"] == "committer@example.test"
