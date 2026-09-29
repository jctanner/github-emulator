"""user.contributionsCollection: GitHub's contribution calendar, from the
rows the emulator keeps (indexed commits, issues, pull requests, reviews)."""

from datetime import datetime

import pytest
from sqlalchemy import select

from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.review import Review
from app.models.search_index import CommitMetadata
from tests.conftest import auth_headers

API = "/api/v3"

QUERY = """
query($login: String!, $from: DateTime, $to: DateTime) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      startedAt
      endedAt
      hasAnyContributions
      totalCommitContributions
      totalIssueContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      contributionCalendar {
        totalContributions
        weeks { firstDay contributionDays { date contributionCount weekday } }
      }
    }
  }
}
"""


async def _seed(client, db_session, test_user, test_token):
    created = await client.post(f"{API}/user/repos", json={"name": "calendar", "auto_init": True}, headers=auth_headers(test_token))
    assert created.status_code in (200, 201), created.text
    repo = (await db_session.execute(select(Repository).where(Repository.full_name == "testuser/calendar"))).scalar_one()
    other = (await client.post(f"{API}/user/repos", json={"name": "fork-of-calendar"}, headers=auth_headers(test_token))).json()["id"]

    # Commits: two by email on 2026-03-02, one by login on 2026-03-04, the
    # first sha again in another repository (a fork's copy: not a second
    # contribution), one by somebody else, one before the window.
    db_session.add_all([
        CommitMetadata(repo_id=repo.id, commit_sha="a" * 40, author_name="Test User", author_email="TEST@test.com", author_date="2026-03-02T10:00:00+00:00"),
        CommitMetadata(repo_id=repo.id, commit_sha="b" * 40, author_name="Test User", author_email="test@test.com", author_date="2026-03-02T23:30:00-02:00"),
        CommitMetadata(repo_id=repo.id, commit_sha="c" * 40, author_name="testuser", author_email="", author_date="2026-03-04T08:00:00+00:00"),
        CommitMetadata(repo_id=other, commit_sha="a" * 40, author_name="Test User", author_email="test@test.com", author_date="2026-03-02T10:00:00+00:00"),
        CommitMetadata(repo_id=repo.id, commit_sha="d" * 40, author_name="Someone Else", author_email="else@test.com", author_date="2026-03-02T10:00:00+00:00"),
        CommitMetadata(repo_id=repo.id, commit_sha="e" * 40, author_name="Test User", author_email="test@test.com", author_date="2026-02-20T10:00:00+00:00"),
    ])
    # An issue on the 3rd, a pull request on the 4th, a review on the 5th.
    issue = Issue(repo_id=repo.id, number=1, user_id=test_user.id, title="an issue", created_at=datetime(2026, 3, 3, 12, 0))
    pr_issue = Issue(repo_id=repo.id, number=2, user_id=test_user.id, title="a pull", created_at=datetime(2026, 3, 4, 12, 0))
    db_session.add_all([issue, pr_issue])
    await db_session.flush()
    pull = PullRequest(issue_id=pr_issue.id, repo_id=repo.id, head_ref="topic", head_sha="f" * 40, base_ref="main", base_sha="0" * 40)
    db_session.add(pull)
    await db_session.flush()
    db_session.add(Review(pull_request_id=pull.id, user_id=test_user.id, state="APPROVED", commit_id="f" * 40, submitted_at=datetime(2026, 3, 5, 9, 0)))
    await db_session.commit()


@pytest.mark.asyncio
async def test_contributions_calendar_counts_what_github_counts(client, db_session, test_user, test_token):
    await _seed(client, db_session, test_user, test_token)

    resp = await client.post("/graphql", json={
        "query": QUERY,
        "variables": {"login": "testuser", "from": "2026-03-01T00:00:00Z", "to": "2026-03-07T00:00:00Z"},
    }, headers=auth_headers(test_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "errors" not in body, body
    coll = body["data"]["user"]["contributionsCollection"]

    # a and b (email, case-insensitive; the second lands on the 3rd in UTC) and
    # c (login); a's copy in the other repository and e (before the window)
    # do not count; d is somebody else's.
    assert coll["totalCommitContributions"] == 3
    assert coll["totalIssueContributions"] == 1
    assert coll["totalPullRequestContributions"] == 1
    assert coll["totalPullRequestReviewContributions"] == 1
    assert coll["hasAnyContributions"] is True

    calendar = coll["contributionCalendar"]
    assert calendar["totalContributions"] == 6
    days = {d["date"]: d for w in calendar["weeks"] for d in w["contributionDays"]}
    assert days["2026-03-02"]["contributionCount"] == 1   # commit a
    assert days["2026-03-03"]["contributionCount"] == 2   # commit b (01:30 UTC) + issue
    assert days["2026-03-04"]["contributionCount"] == 2   # commit c + pull request
    assert days["2026-03-05"]["contributionCount"] == 1   # review
    assert days["2026-03-06"]["contributionCount"] == 0
    # 2026-03-01 is a Sunday, so the calendar starts on the window's first day.
    assert calendar["weeks"][0]["firstDay"] == "2026-03-01"
    assert days["2026-03-01"]["weekday"] == 0
    assert days["2026-03-07"]["weekday"] == 6
    assert sum(d["contributionCount"] for d in days.values()) == calendar["totalContributions"]


@pytest.mark.asyncio
async def test_calendar_starts_on_the_sunday_before_from(client, db_session, test_user, test_token):
    await _seed(client, db_session, test_user, test_token)
    resp = await client.post("/graphql", json={
        "query": QUERY,
        "variables": {"login": "testuser", "from": "2026-03-04T00:00:00Z", "to": "2026-03-10T00:00:00Z"},
    }, headers=auth_headers(test_token))
    calendar = resp.json()["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    first = calendar["weeks"][0]
    assert first["firstDay"] == "2026-03-01"
    assert [d["weekday"] for d in first["contributionDays"]] == list(range(7))
    assert len(calendar["weeks"]) == 2
    assert calendar["weeks"][1]["firstDay"] == "2026-03-08"


@pytest.mark.asyncio
async def test_window_defaults_to_the_year_ending_now_and_is_capped_at_a_year(client, test_user, test_token):
    resp = await client.post("/graphql", json={"query": QUERY, "variables": {"login": "testuser"}}, headers=auth_headers(test_token))
    coll = resp.json()["data"]["user"]["contributionsCollection"]
    started = datetime.fromisoformat(coll["startedAt"])
    ended = datetime.fromisoformat(coll["endedAt"])
    assert (ended - started).days == 365
    assert coll["hasAnyContributions"] is False
    assert coll["contributionCalendar"]["totalContributions"] == 0

    too_long = await client.post("/graphql", json={
        "query": QUERY,
        "variables": {"login": "testuser", "from": "2024-01-01T00:00:00Z", "to": "2026-01-02T00:00:00Z"},
    }, headers=auth_headers(test_token))
    body = too_long.json()
    assert body.get("errors"), body
    assert "must not exceed 1 year" in body["errors"][0]["message"]


@pytest.mark.asyncio
async def test_the_team_tracker_query_shape_is_served(client, db_session, test_user, test_token):
    """The query Org Pulse's team-tracker sends, aliases and all."""
    await _seed(client, db_session, test_user, test_token)
    query = 'query { u0: user(login: "testuser") { contributionsCollection { contributionCalendar { totalContributions weeks { contributionDays { date contributionCount } } } } } }'
    resp = await client.post("/graphql", json={"query": query}, headers=auth_headers(test_token))
    body = resp.json()
    assert "errors" not in body, body
    calendar = body["data"]["u0"]["contributionsCollection"]["contributionCalendar"]
    assert isinstance(calendar["totalContributions"], int)
    assert calendar["weeks"] and calendar["weeks"][0]["contributionDays"][0]["date"]
