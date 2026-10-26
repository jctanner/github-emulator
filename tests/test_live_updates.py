"""Open issue and pull request pages learn about changes from any writer."""

import asyncio

import pytest
from sqlalchemy import delete, select

from app.models.issue import Issue, IssueAssignee
from app.models.pull_request import PullRequest
from app.services.live_updates import Subscription
from tests.conftest import API, auth_headers


async def _issue_id(db, repo, number):
    return (
        await db.execute(
            select(Issue.id).where(Issue.number == number, Issue.repository.has(name=repo))
        )
    ).scalar_one()


async def _changed(subscription) -> bool:
    # Notification runs through call_soon_threadsafe; let it land.
    await asyncio.sleep(0)
    return await subscription.wait(0.2)


@pytest.mark.asyncio
async def test_a_comment_wakes_only_its_issue(client, db_session, test_user, test_token):
    await client.post(f"{API}/user/repos", json={"name": "live"}, headers=auth_headers(test_token))
    for title in ("watched", "other"):
        await client.post(
            f"{API}/repos/testuser/live/issues", json={"title": title}, headers=auth_headers(test_token)
        )
    watched = Subscription([("issue", await _issue_id(db_session, "live", 1))])
    other = Subscription([("issue", await _issue_id(db_session, "live", 2))])
    with watched, other:
        resp = await client.post(
            f"{API}/repos/testuser/live/issues/1/comments",
            json={"body": "hello"},
            headers=auth_headers(test_token),
        )
        assert resp.status_code == 201
        assert await _changed(watched)
        assert not await _changed(other)

        await client.patch(
            f"{API}/repos/testuser/live/issues/1",
            json={"state": "closed"},
            headers=auth_headers(test_token),
        )
        assert await _changed(watched)


@pytest.mark.asyncio
async def test_a_bare_bulk_delete_wakes_the_issue(client, db_session, test_user, test_token):
    # Clearing labels or assignees can be a lone DELETE with no ORM objects
    # in the session; only its WHERE clause names the issue.
    await client.post(f"{API}/user/repos", json={"name": "live-bulk"}, headers=auth_headers(test_token))
    await client.post(
        f"{API}/repos/testuser/live-bulk/issues", json={"title": "t"}, headers=auth_headers(test_token)
    )
    issue_id = await _issue_id(db_session, "live-bulk", 1)
    with Subscription([("issue", issue_id)]) as watched:
        await db_session.execute(delete(IssueAssignee).where(IssueAssignee.issue_id == issue_id))
        await db_session.commit()
        assert await _changed(watched)


@pytest.mark.asyncio
async def test_a_review_wakes_its_pull_request(
    client, db_session, test_user, test_token, test_repo_with_init
):
    owner, repo, _ = test_repo_with_init
    resp = await client.post(
        f"{API}/repos/{owner}/{repo}/pulls",
        json={"title": "change", "head": "feature", "base": "main"},
        headers=auth_headers(test_token),
    )
    number = resp.json()["number"]
    pull_id = (
        await db_session.execute(select(PullRequest.id).where(PullRequest.issue_id == await _issue_id(db_session, repo, number)))
    ).scalar_one()
    with Subscription([("pull", pull_id)]) as watched:
        resp = await client.post(
            f"{API}/repos/{owner}/{repo}/pulls/{number}/reviews",
            json={"event": "COMMENT", "body": "looks fine"},
            headers=auth_headers(test_token),
        )
        assert resp.status_code == 201
        assert await _changed(watched)


@pytest.mark.asyncio
async def test_a_rolled_back_change_wakes_nobody(db_session, test_user, test_token, client):
    await client.post(f"{API}/user/repos", json={"name": "live-rollback"}, headers=auth_headers(test_token))
    await client.post(
        f"{API}/repos/testuser/live-rollback/issues", json={"title": "t"}, headers=auth_headers(test_token)
    )
    issue = (await db_session.execute(select(Issue).where(Issue.repository.has(name="live-rollback")))).scalar_one()
    with Subscription([("issue", issue.id)]) as watched:
        issue.title = "never saved"
        await db_session.flush()
        await db_session.rollback()
        assert not await _changed(watched)


@pytest.mark.asyncio
async def test_live_endpoint_hides_unknown_issues(client, test_user, test_token):
    await client.post(f"{API}/user/repos", json={"name": "live-404"}, headers=auth_headers(test_token))
    resp = await client.get("/api/_ui/repos/testuser/live-404/issues/9/live", headers=auth_headers(test_token))
    assert resp.status_code == 404
