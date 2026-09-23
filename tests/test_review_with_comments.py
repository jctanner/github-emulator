"""A review submitted with its inline comments in one request.

GitHub's POST /repos/{o}/{r}/pulls/{n}/reviews accepts a `comments` array and
creates a review comment for each entry, which is how a client posts a
multi-file review atomically instead of as N+1 calls.

The emulator accepted that array and discarded it. The review came back with
the right state and body, the caller reported success, and every inline
finding was gone -- the failure only showed up by asking the API for comments
that the client had been told were attached.
"""

import pytest

from tests.conftest import auth_headers

API = "/api/v3"


async def _create_pr(client, token, repo_name):
    await client.post(
        f"{API}/user/repos", json={"name": repo_name}, headers=auth_headers(token)
    )
    await client.post(
        f"{API}/repos/testuser/{repo_name}/issues",
        json={"title": "Test Issue"},
        headers=auth_headers(token),
    )
    resp = await client.post(
        f"{API}/repos/testuser/{repo_name}/pulls",
        json={"title": "Test PR", "head": "feature", "base": "main"},
        headers=auth_headers(token),
    )
    return resp.json()


@pytest.mark.asyncio
async def test_review_comments_are_created_and_readable(client, test_user, test_token):
    """The comments arrive, are attached to the review, and are listed."""
    pr = await _create_pr(client, test_token, "rwc-create")
    number = pr["number"]

    resp = await client.post(
        f"{API}/repos/testuser/rwc-create/pulls/{number}/reviews",
        json={
            "body": "Two problems.",
            "event": "REQUEST_CHANGES",
            "comments": [
                {"path": "scripts/retry.py", "line": 6, "body": "Mutable default."},
                {"path": "scripts/retry.py", "position": 14, "body": "Bare except."},
            ],
        },
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201, resp.text
    review = resp.json()
    assert review["state"] == "CHANGES_REQUESTED"

    listed = await client.get(
        f"{API}/repos/testuser/rwc-create/pulls/{number}/comments",
        headers=auth_headers(test_token),
    )
    assert listed.status_code == 200
    comments = listed.json()
    assert len(comments) == 2, "the review's inline comments were dropped"
    assert {c["body"] for c in comments} == {"Mutable default.", "Bare except."}
    assert {c["path"] for c in comments} == {"scripts/retry.py"}

    # Both addressing forms survive: the newer `line` and the legacy `position`.
    by_body = {c["body"]: c for c in comments}
    assert by_body["Mutable default."]["line"] == 6
    assert by_body["Bare except."]["position"] == 14

    on_review = await client.get(
        f"{API}/repos/testuser/rwc-create/pulls/{number}"
        f"/reviews/{review['id']}/comments",
        headers=auth_headers(test_token),
    )
    assert on_review.status_code == 200
    assert len(on_review.json()) == 2, "comments are not attached to the review"


@pytest.mark.asyncio
async def test_review_without_comments_is_unchanged(client, test_user, test_token):
    """The common case keeps working: no array, no comments, no error."""
    pr = await _create_pr(client, test_token, "rwc-none")
    number = pr["number"]
    resp = await client.post(
        f"{API}/repos/testuser/rwc-none/pulls/{number}/reviews",
        json={"body": "Looks good.", "event": "APPROVE"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    assert resp.json()["state"] == "APPROVED"
    listed = await client.get(
        f"{API}/repos/testuser/rwc-none/pulls/{number}/comments",
        headers=auth_headers(test_token),
    )
    assert listed.json() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "slug, entry, expected",
    [
        ("nopath", {"line": 1, "body": "no path"}, "path is required"),
        ("nobody", {"path": "a.py", "line": 1}, "body is required"),
        ("noanchor", {"path": "a.py", "body": "x"}, "requires either position or line"),
    ],
)
async def test_a_malformed_comment_rejects_the_whole_request(
    client, test_user, test_token, slug, entry, expected
):
    """Rejected whole, not partially applied.

    Validation happens before the review is written, so a bad entry does not
    leave a review behind carrying some of its comments.
    """
    repo = f"rwc-bad-{slug}"
    pr = await _create_pr(client, test_token, repo)
    number = pr["number"]

    resp = await client.post(
        f"{API}/repos/testuser/{repo}/pulls/{number}/reviews",
        json={"event": "COMMENT", "body": "x", "comments": [entry]},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 422, resp.text
    assert expected in str(resp.json()), resp.text

    reviews = await client.get(
        f"{API}/repos/testuser/{repo}/pulls/{number}/reviews",
        headers=auth_headers(test_token),
    )
    assert reviews.json() == [], "a rejected request still created a review"
