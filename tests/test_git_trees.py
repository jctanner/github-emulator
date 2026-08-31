"""Tests for Git Data tree creation."""

import base64

import pytest

from tests.conftest import API, auth_headers


async def _base_tree(client, owner, repo, headers):
    ref = await client.get(
        f"{API}/repos/{owner}/{repo}/git/ref/heads/main", headers=headers
    )
    commit = await client.get(
        f"{API}/repos/{owner}/{repo}/git/commits/{ref.json()['object']['sha']}",
        headers=headers,
    )
    return commit.json()["tree"]["sha"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "encoding"),
    [("inline text\n", "utf-8"), (base64.b64encode(b"binary\x00data").decode(), "base64")],
)
async def test_create_tree_supports_inline_content(
    client, test_token, test_repo_with_init, content, encoding
):
    owner, repo, _ = test_repo_with_init
    headers = auth_headers(test_token)
    response = await client.post(
        f"{API}/repos/{owner}/{repo}/git/trees",
        json={
            "tree": [
                {
                    "path": "inline.dat",
                    "mode": "100644",
                    "type": "blob",
                    "content": content,
                    "encoding": encoding,
                }
            ]
        },
        headers=headers,
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_create_tree_supports_nested_paths_and_preserves_base(
    client, test_token, test_repo_with_init
):
    owner, repo, _ = test_repo_with_init
    headers = auth_headers(test_token)
    base_tree = await _base_tree(client, owner, repo, headers)
    response = await client.post(
        f"{API}/repos/{owner}/{repo}/git/trees",
        json={
            "base_tree": base_tree,
            "tree": [
                {
                    "path": "backend/src/main.py",
                    "mode": "100644",
                    "type": "blob",
                    "content": "print('hello')\n",
                }
            ],
        },
        headers=headers,
    )
    assert response.status_code == 201
    tree = await client.get(
        f"{API}/repos/{owner}/{repo}/git/trees/{response.json()['sha']}?recursive=1",
        headers=headers,
    )
    paths = {entry["path"] for entry in tree.json()["tree"]}
    assert {"README.md", "backend/src/main.py"} <= paths


@pytest.mark.asyncio
async def test_create_tree_rejects_empty_entries(
    client, test_token, test_repo_with_init
):
    owner, repo, _ = test_repo_with_init
    response = await client.post(
        f"{API}/repos/{owner}/{repo}/git/trees",
        json={"tree": []},
        headers=auth_headers(test_token),
    )
    assert response.status_code == 422
