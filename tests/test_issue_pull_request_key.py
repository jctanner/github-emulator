"""`pull_request` distinguishes a pull request from an issue.

GitHub omits the key entirely on a plain issue and includes it on a pull
request. That presence test is how clients filter pull requests out of an
issues listing -- `jq 'has("pull_request")'` and the equivalent in every SDK.

The emulator got it wrong in both directions: the listing emitted the key as
null on plain issues, so every issue looked like a pull request, while the
single-issue route stripped it from pull requests, so a pull request fetched
through the issues API looked like an issue.
"""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


async def _repo_with_both(client, token, name):
    """A repository holding one plain issue and one pull request."""
    await client.post(
        f"{API}/user/repos", json={"name": name}, headers=auth_headers(token)
    )
    issue = (await client.post(
        f"{API}/repos/testuser/{name}/issues",
        json={"title": "a plain issue"}, headers=auth_headers(token),
    )).json()
    await client.post(
        f"{API}/repos/testuser/{name}/issues",
        json={"title": "backing issue"}, headers=auth_headers(token),
    )
    pr = (await client.post(
        f"{API}/repos/testuser/{name}/pulls",
        json={"title": "a pull request", "head": "feature", "base": "main"},
        headers=auth_headers(token),
    )).json()
    return issue, pr


@pytest.mark.asyncio
async def test_listing_marks_only_pull_requests(client, test_user, test_token):
    issue, pr = await _repo_with_both(client, test_token, "prk-list")
    listed = (await client.get(
        f"{API}/repos/testuser/prk-list/issues?state=all&per_page=100",
        headers=auth_headers(test_token),
    )).json()
    by_number = {i["number"]: i for i in listed}

    assert "pull_request" not in by_number[issue["number"]], \
        "a plain issue carries the key, so every issue reads as a pull request"
    assert "pull_request" in by_number[pr["number"]], \
        "the pull request is missing its marker"
    assert by_number[pr["number"]]["pull_request"]["html_url"].endswith(
        f"/pull/{pr['number']}"
    )

    # The filter clients actually write.
    pulls = [i for i in listed if "pull_request" in i]
    assert [i["number"] for i in pulls] == [pr["number"]]


@pytest.mark.asyncio
async def test_single_issue_route_agrees_with_the_listing(client, test_user, test_token):
    """Fetching either one directly must give the same answer as the listing."""
    issue, pr = await _repo_with_both(client, test_token, "prk-single")

    one = (await client.get(
        f"{API}/repos/testuser/prk-single/issues/{issue['number']}",
        headers=auth_headers(test_token),
    )).json()
    assert "pull_request" not in one

    other = (await client.get(
        f"{API}/repos/testuser/prk-single/issues/{pr['number']}",
        headers=auth_headers(test_token),
    )).json()
    assert "pull_request" in other, \
        "a pull request fetched through the issues API looks like an issue"
    assert other["pull_request"]["html_url"].endswith(f"/pull/{pr['number']}")


@pytest.mark.asyncio
async def test_other_nullable_fields_are_still_present(client, test_user, test_token):
    """exclude_unset must not take the nulls GitHub does send.

    `assignee`, `milestone`, `closed_at` and `body` are null on a fresh issue
    and GitHub includes them. Dropping those to fix pull_request would trade
    one divergence for four.
    """
    issue, _ = await _repo_with_both(client, test_token, "prk-nulls")
    one = (await client.get(
        f"{API}/repos/testuser/prk-nulls/issues/{issue['number']}",
        headers=auth_headers(test_token),
    )).json()
    for field in ("assignee", "milestone", "closed_at", "body", "state_reason"):
        assert field in one, f"{field} went missing from the response"
