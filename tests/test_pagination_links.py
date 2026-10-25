"""Pagination Link headers keep the request's query parameters."""

import re
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


def _links(resp) -> dict[str, str]:
    """Map each rel of the response's Link header to its URL."""
    header = resp.headers.get("link", "")
    return {rel: url for url, rel in re.findall(r'<([^>]+)>; rel="(\w+)"', header)}


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def _assert_links_keep(links: dict[str, str], rels: set[str], expected: dict[str, str]):
    assert set(links) == rels
    for rel, url in links.items():
        query = _query(url)
        for key, value in expected.items():
            assert query[key] == [value], (rel, url)


async def _create_repo(client, token, name):
    resp = await client.post(
        f"{API}/user/repos", json={"name": name}, headers=auth_headers(token)
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_issue_links_keep_state_filter(client, test_user, test_token):
    """Following next with state=all pages through all issues, not open ones."""
    await _create_repo(client, test_token, "page-issues")
    for title in ("one", "two", "three"):
        await client.post(
            f"{API}/repos/testuser/page-issues/issues",
            json={"title": title},
            headers=auth_headers(test_token),
        )
    for number in (1, 2):
        await client.patch(
            f"{API}/repos/testuser/page-issues/issues/{number}",
            json={"state": "closed"},
            headers=auth_headers(test_token),
        )

    first = await client.get(
        f"{API}/repos/testuser/page-issues/issues",
        params={"state": "all", "direction": "desc", "per_page": 2},
    )
    links = _links(first)
    _assert_links_keep(
        links, {"next", "last"}, {"state": "all", "direction": "desc", "per_page": "2"}
    )
    assert _query(links["next"])["page"] == ["2"]

    second = await client.get(links["next"])
    titles = [i["title"] for i in first.json()] + [i["title"] for i in second.json()]
    assert sorted(titles) == ["one", "three", "two"]
    _assert_links_keep(
        _links(second), {"prev", "first"}, {"state": "all", "direction": "desc"}
    )
    assert _query(_links(second)["first"])["page"] == ["1"]


@pytest.mark.asyncio
async def test_pull_links_keep_state_filter(client, test_user, test_token):
    await _create_repo(client, test_token, "page-pulls")
    for n in range(3):
        resp = await client.post(
            f"{API}/repos/testuser/page-pulls/pulls",
            json={"title": f"pr {n}", "head": f"feature-{n}", "base": "main"},
            headers=auth_headers(test_token),
        )
        assert resp.status_code == 201
    for number in (1, 2):
        await client.patch(
            f"{API}/repos/testuser/page-pulls/pulls/{number}",
            json={"state": "closed"},
            headers=auth_headers(test_token),
        )

    first = await client.get(
        f"{API}/repos/testuser/page-pulls/pulls",
        params={"state": "all", "per_page": 2},
    )
    links = _links(first)
    _assert_links_keep(links, {"next", "last"}, {"state": "all", "per_page": "2"})

    second = await client.get(links["next"])
    numbers = [p["number"] for p in first.json()] + [p["number"] for p in second.json()]
    assert sorted(numbers) == [1, 2, 3]


@pytest.mark.asyncio
async def test_issue_comment_links_keep_since(client, test_user, test_token):
    await _create_repo(client, test_token, "page-comments")
    await client.post(
        f"{API}/repos/testuser/page-comments/issues",
        json={"title": "discussed"},
        headers=auth_headers(test_token),
    )
    for n in range(3):
        await client.post(
            f"{API}/repos/testuser/page-comments/issues/1/comments",
            json={"body": f"comment {n}"},
            headers=auth_headers(test_token),
        )

    since = "2000-01-01T00:00:00Z"
    first = await client.get(
        f"{API}/repos/testuser/page-comments/issues/1/comments",
        params={"since": since, "per_page": 2},
    )
    links = _links(first)
    _assert_links_keep(links, {"next", "last"}, {"since": since, "per_page": "2"})

    second = await client.get(links["next"])
    assert len(first.json()) + len(second.json()) == 3
    _assert_links_keep(_links(second), {"prev", "first"}, {"since": since})


@pytest.mark.asyncio
async def test_review_comment_links_keep_sort(client, test_user, test_token):
    await _create_repo(client, test_token, "page-review")
    resp = await client.post(
        f"{API}/repos/testuser/page-review/pulls",
        json={"title": "reviewed", "head": "feature", "base": "main"},
        headers=auth_headers(test_token),
    )
    number = resp.json()["number"]
    for n in range(3):
        await client.post(
            f"{API}/repos/testuser/page-review/pulls/{number}/comments",
            json={"body": f"note {n}", "path": "file.py", "commit_id": "abc"},
            headers=auth_headers(test_token),
        )

    first = await client.get(
        f"{API}/repos/testuser/page-review/pulls/{number}/comments",
        params={"sort": "created", "direction": "asc", "per_page": 2},
    )
    links = _links(first)
    _assert_links_keep(
        links, {"next", "last"}, {"sort": "created", "direction": "asc", "per_page": "2"}
    )

    second = await client.get(links["next"])
    bodies = [c["body"] for c in first.json()] + [c["body"] for c in second.json()]
    assert bodies == ["note 0", "note 1", "note 2"]


@pytest.mark.asyncio
async def test_repo_links_keep_type_and_sort(client, test_user, test_token):
    for name in ("page-repo-a", "page-repo-b"):
        await _create_repo(client, test_token, name)

    first = await client.get(
        f"{API}/users/testuser/repos",
        params={"type": "sources", "sort": "full_name", "per_page": 1},
    )
    links = _links(first)
    _assert_links_keep(
        links, {"next", "last"}, {"type": "sources", "sort": "full_name", "per_page": "1"}
    )

    second = await client.get(links["next"])
    names = [r["name"] for r in first.json()] + [r["name"] for r in second.json()]
    assert names == ["page-repo-a", "page-repo-b"]
