"""Mentioning an issue adds a cross-referenced event to its timeline."""

import pytest

from app.config import settings
from app.services.cross_references import parse_references
from tests.conftest import API, auth_headers


@pytest.fixture
async def outsider_token(db_session):
    """A user with an account and no access to private repositories."""
    import hashlib
    import secrets

    from app.models.token import PersonalAccessToken
    from app.models.user import User

    user = User(
        login="outsider",
        hashed_password=hashlib.sha256(b"password").hexdigest(),
        email="outsider@localhost",
        name="Outsider",
        site_admin=False,
    )
    db_session.add(user)
    await db_session.flush()
    raw = f"ghp_{secrets.token_hex(20)}"
    db_session.add(
        PersonalAccessToken(
            user_id=user.id,
            name="probe",
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            token_prefix=raw[:8],
            scopes=["repo", "user"],
        )
    )
    await db_session.commit()
    return raw


async def _repo(client, headers, name, private=False):
    resp = await client.post(
        f"{API}/user/repos", json={"name": name, "private": private}, headers=headers
    )
    assert resp.status_code == 201


async def _issue(client, headers, repo, title, body=None):
    resp = await client.post(
        f"{API}/repos/testuser/{repo}/issues",
        json={"title": title, "body": body},
        headers=headers,
    )
    assert resp.status_code == 201
    return resp.json()["number"]


async def _timeline(client, headers, repo, number):
    resp = await client.get(f"{API}/repos/testuser/{repo}/issues/{number}/timeline", headers=headers)
    assert resp.status_code == 200
    return resp.json()


def _cross_references(timeline):
    return [item for item in timeline if item["event"] == "cross-referenced"]


def test_parse_references_finds_mentions_outside_code():
    base = settings.BASE_URL
    text = (
        "Fixes #1, see other/repo#2 and #1 again.\n"
        f"Also {base}/other/repo/issues/3 and {base}/ui/other/repo/pull/4.\n"
        "Not `#5`, not a#6, not &#7; and not https://elsewhere.example/o/r/issues/8.\n"
        "```\n#9\n```\n"
    )
    assert parse_references(text) == [
        ("other/repo", 3),
        ("other/repo", 4),
        (None, 1),
        ("other/repo", 2),
    ]


@pytest.mark.asyncio
async def test_a_pull_request_mentioning_an_issue_appears_on_its_timeline(client, test_token):
    headers = auth_headers(test_token)
    await _repo(client, headers, "xref")
    await _issue(client, headers, "xref", "Bug")
    pull = await client.post(
        f"{API}/repos/testuser/xref/pulls",
        json={"title": "Fix the bug", "body": "Fixes #1", "head": "fix", "base": "main"},
        headers=headers,
    )
    assert pull.status_code == 201

    [reference] = _cross_references(await _timeline(client, headers, "xref", 1))
    assert reference["actor"]["login"] == "testuser"
    assert reference["source"]["type"] == "issue"
    source = reference["source"]["issue"]
    assert source["number"] == 2
    assert source["title"] == "Fix the bug"
    assert "pull_request" in source
    assert "id" not in reference

    # GitHub lists cross-references only in the timeline, not in /events.
    events = await client.get(f"{API}/repos/testuser/xref/issues/1/events", headers=headers)
    assert events.json() == []


@pytest.mark.asyncio
async def test_comments_and_edits_reference_once_per_source(client, test_token):
    headers = auth_headers(test_token)
    await _repo(client, headers, "xref-once")
    await _issue(client, headers, "xref-once", "Target")
    source = await _issue(client, headers, "xref-once", "Source", body="Related to #1")
    # Mentioning it again, in a comment or an edit, adds nothing more.
    await client.post(
        f"{API}/repos/testuser/xref-once/issues/{source}/comments",
        json={"body": "As I said, #1."},
        headers=headers,
    )
    await client.patch(
        f"{API}/repos/testuser/xref-once/issues/{source}",
        json={"body": "Still about #1 and #1"},
        headers=headers,
    )
    # A comment on a third issue is a new source.
    third = await _issue(client, headers, "xref-once", "Third")
    resp = await client.post(
        f"{API}/repos/testuser/xref-once/issues/{third}/comments",
        json={"body": "Blocked by #1"},
        headers=headers,
    )
    assert resp.status_code == 201

    references = _cross_references(await _timeline(client, headers, "xref-once", 1))
    assert [item["source"]["issue"]["number"] for item in references] == [source, third]


@pytest.mark.asyncio
async def test_an_edit_that_adds_a_mention_references_the_issue(client, test_token):
    headers = auth_headers(test_token)
    await _repo(client, headers, "xref-edit")
    await _issue(client, headers, "xref-edit", "Target")
    source = await _issue(client, headers, "xref-edit", "Source", body="Nothing yet")
    assert _cross_references(await _timeline(client, headers, "xref-edit", 1)) == []

    await client.patch(
        f"{API}/repos/testuser/xref-edit/issues/{source}",
        json={"body": "Now about #1"},
        headers=headers,
    )
    assert len(_cross_references(await _timeline(client, headers, "xref-edit", 1))) == 1


@pytest.mark.asyncio
async def test_self_missing_and_code_mentions_reference_nothing(client, test_token):
    headers = auth_headers(test_token)
    await _repo(client, headers, "xref-none")
    await _issue(client, headers, "xref-none", "Self", body="This is #1, see #99 and `#1`")
    assert _cross_references(await _timeline(client, headers, "xref-none", 1)) == []


@pytest.mark.asyncio
async def test_references_across_repositories_respect_visibility(
    client, test_token, outsider_token
):
    headers = auth_headers(test_token)
    await _repo(client, headers, "xref-public")
    await _issue(client, headers, "xref-public", "Shared target")
    await _repo(client, headers, "xref-private", private=True)
    await _issue(
        client, headers, "xref-private", "Secret work", body="Needs testuser/xref-public#1"
    )

    # The owner sees the reference from their private repository...
    [reference] = _cross_references(await _timeline(client, headers, "xref-public", 1))
    assert reference["source"]["issue"]["title"] == "Secret work"

    # ...and someone who cannot see that repository does not.
    assert _cross_references(
        await _timeline(client, auth_headers(outsider_token), "xref-public", 1)
    ) == []


@pytest.mark.asyncio
async def test_timeline_merges_comments_and_events_in_order(client, test_token):
    headers = auth_headers(test_token)
    await _repo(client, headers, "xref-order")
    await _issue(client, headers, "xref-order", "Target")
    await client.post(
        f"{API}/repos/testuser/xref-order/issues/1/comments",
        json={"body": "first"},
        headers=headers,
    )
    await _issue(client, headers, "xref-order", "Mentions", body="#1")
    timeline = await _timeline(client, headers, "xref-order", 1)
    assert [item["event"] for item in timeline] == ["commented", "cross-referenced"]
    assert timeline[0]["body"] == "first"
    assert timeline[0]["actor"]["login"] == "testuser"
