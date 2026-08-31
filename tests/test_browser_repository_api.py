"""UI-oriented repository summary API tests."""

import pytest

from tests.conftest import auth_headers

UI_API = "/api/_ui"
API = "/api/v3"


@pytest.mark.asyncio
async def test_repository_home_summary_returns_counts_without_collections(
    client, test_repo_with_init
):
    owner, repo, _ = test_repo_with_init
    response = await client.get(f"{UI_API}/repos/{owner}/{repo}/summary")

    assert response.status_code == 200
    assert response.json() == {
        "default_branch": "main",
        "commit_count": 1,
        "branch_count": 1,
        "tag_count": 0,
    }


@pytest.mark.asyncio
async def test_repository_home_summary_does_not_cap_commit_count(
    client, test_repo_with_init, monkeypatch
):
    owner, repo, _ = test_repo_with_init

    async def count_commits(_disk_path: str, _ref: str) -> int:
        return 137

    monkeypatch.setattr(
        "app.api.browser_repositories.get_commit_count", count_commits
    )
    response = await client.get(f"{UI_API}/repos/{owner}/{repo}/summary")

    assert response.status_code == 200
    assert response.json()["commit_count"] == 137


@pytest.mark.asyncio
async def test_repository_home_summary_counts_requested_ref(
    client, test_repo_with_init, monkeypatch
):
    owner, repo, _ = test_repo_with_init
    counted_refs = []

    async def count_commits(_disk_path: str, ref: str) -> int:
        counted_refs.append(ref)
        return 3

    monkeypatch.setattr(
        "app.api.browser_repositories.get_commit_count", count_commits
    )
    response = await client.get(
        f"{UI_API}/repos/{owner}/{repo}/summary?ref=feature"
    )

    assert response.status_code == 200
    assert response.json()["commit_count"] == 3
    assert counted_refs == ["feature"]


@pytest.mark.asyncio
async def test_repository_navigation_counts_issues_and_pulls_separately(
    client, test_repo_with_init, test_token
):
    owner, repo, _ = test_repo_with_init
    issue = await client.post(
        f"{API}/repos/{owner}/{repo}/issues",
        json={"title": "Open issue"},
        headers=auth_headers(test_token),
    )
    assert issue.status_code == 201
    pull = await client.post(
        f"{API}/repos/{owner}/{repo}/pulls",
        json={"title": "Open pull", "head": "main", "base": "main"},
        headers=auth_headers(test_token),
    )
    assert pull.status_code == 201

    response = await client.get(f"{UI_API}/repos/{owner}/{repo}/navigation")
    assert response.status_code == 200
    assert response.json() == {
        "open_issues_count": 1,
        "open_pulls_count": 1,
    }
