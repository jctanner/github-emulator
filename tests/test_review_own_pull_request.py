"""A pull request's author cannot approve it or request changes on it.

GitHub refuses both with 422 and a plain-string error:

    {"message": "Unprocessable Entity",
     "errors": ["Review Can not request changes on your own pull request"]}

and "Review Can not approve your own pull request" for APPROVE. A COMMENT
review by the author is allowed. The same rule applies when the author
submits a pending review. The emulator accepted all of them, so a client's
self-review guard was never exercised. A review that is created answers 200,
as GitHub documents, not 201.
"""

import pytest

from tests.conftest import auth_headers
from tests.test_org_memberships_api import _make_user
from tests.test_review_with_comments import _create_pr

API = "/api/v3"

pytestmark = pytest.mark.asyncio

REFUSED = {
    "APPROVE": "Review Can not approve your own pull request",
    "REQUEST_CHANGES": "Review Can not request changes on your own pull request",
}


def _reviews(repo, number):
    return f"{API}/repos/testuser/{repo}/pulls/{number}/reviews"


@pytest.mark.parametrize("event", ["APPROVE", "REQUEST_CHANGES"])
async def test_the_author_cannot_approve_or_request_changes(client, test_user, test_token, event):
    pr = await _create_pr(client, test_token, "own-review")

    resp = await client.post(_reviews("own-review", pr["number"]), json={"event": event, "body": "x"},
                             headers=auth_headers(test_token))

    assert resp.status_code == 422, resp.text
    assert resp.json()["message"] == "Unprocessable Entity"
    assert resp.json()["errors"] == [REFUSED[event]]
    listed = await client.get(_reviews("own-review", pr["number"]), headers=auth_headers(test_token))
    assert listed.json() == []


async def test_the_author_may_comment(client, test_user, test_token):
    pr = await _create_pr(client, test_token, "own-comment")
    resp = await client.post(_reviews("own-comment", pr["number"]), json={"event": "COMMENT", "body": "note"},
                             headers=auth_headers(test_token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "COMMENTED"


async def test_another_user_may_approve_and_gets_200(client, test_user, test_token, db_session):
    pr = await _create_pr(client, test_token, "other-review")
    reviewer = await _make_user(db_session, "reviewer")
    resp = await client.post(_reviews("other-review", pr["number"]), json={"event": "APPROVE"},
                             headers=auth_headers(reviewer))
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "APPROVED"


@pytest.mark.parametrize("method", ["post", "put"])
@pytest.mark.parametrize("event", ["APPROVE", "REQUEST_CHANGES"])
async def test_the_author_cannot_submit_a_pending_review_as_approve_or_request_changes(
    client, test_user, test_token, event, method,
):
    pr = await _create_pr(client, test_token, "own-pending")
    pending = await client.post(_reviews("own-pending", pr["number"]), json={"body": "draft"},
                                headers=auth_headers(test_token))
    assert pending.status_code == 200, pending.text
    review_id = pending.json()["id"]

    submit = getattr(client, method)
    url = f"{_reviews('own-pending', pr['number'])}/{review_id}/events"
    refused = await submit(url, json={"event": event}, headers=auth_headers(test_token))
    assert refused.status_code == 422, refused.text
    assert refused.json()["errors"] == [REFUSED[event]]
    still = await client.get(f"{_reviews('own-pending', pr['number'])}/{review_id}", headers=auth_headers(test_token))
    assert still.json()["state"] == "PENDING"

    commented = await submit(url, json={"event": "COMMENT"}, headers=auth_headers(test_token))
    assert commented.status_code == 200, commented.text
    assert commented.json()["state"] == "COMMENTED"
