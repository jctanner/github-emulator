"""Response counters reflect stored comments and the real Git comparison."""

import pytest
from sqlalchemy import select

from app.git.bare_repo import write_file
from app.models.repository import Repository
from tests.conftest import API, auth_headers
from tests.test_pulls_api import _create_real_pr_with_diff

pytestmark = pytest.mark.asyncio


async def _assert_comments(client, root, number, expected, is_pr):
    response = await client.get(f"{root}/issues/{number}")
    assert response.status_code == 200
    assert response.json()["comments"] == expected
    response = await client.get(root + "/issues")
    assert response.status_code == 200
    assert next(item for item in response.json() if item["number"] == number)["comments"] == expected
    response = await client.get(API + "/search/issues", params={"q": "counter-subject"})
    assert response.status_code == 200
    assert next(item for item in response.json()["items"] if item["number"] == number)["comments"] == expected
    if is_pr:
        response = await client.get(f"{root}/pulls/{number}")
        assert response.status_code == 200
        assert response.json()["comments"] == expected
        response = await client.get(root + "/pulls")
        assert response.status_code == 200
        assert next(item for item in response.json() if item["number"] == number)["comments"] == expected


@pytest.mark.parametrize("is_pr", [False, True])
async def test_comment_counts_after_create_edit_and_delete(client, db_session, test_token, is_pr):
    headers = auth_headers(test_token)
    name = await _create_real_pr_with_diff(client, db_session, test_token)
    root = f"{API}/repos/testuser/{name}"
    if is_pr:
        response = await client.patch(root + "/pulls/1", json={"title": "counter-subject"}, headers=headers)
        number = 1
    else:
        response = await client.post(root + "/issues", json={"title": "counter-subject"}, headers=headers)
        number = response.json()["number"]
    assert response.status_code in (200, 201)
    await _assert_comments(client, root, number, 0, is_pr)
    comments = []
    for expected in (1, 2):
        response = await client.post(f"{root}/issues/{number}/comments", json={"body": "Comment"}, headers=headers)
        assert response.status_code == 201
        comments.append(response.json()["id"])
        await _assert_comments(client, root, number, expected, is_pr)
    response = await client.patch(f"{root}/issues/comments/{comments[0]}", json={"body": "Edited"}, headers=headers)
    assert response.status_code == 200
    await _assert_comments(client, root, number, 2, is_pr)
    response = await client.patch(f"{root}/issues/{number}", json={"body": "Updated description"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["comments"] == 2
    if is_pr:
        response = await client.patch(f"{root}/pulls/{number}", json={"body": "Updated PR description"}, headers=headers)
        assert response.status_code == 200
        assert response.json()["comments"] == 2
    response = await client.delete(f"{root}/issues/comments/{comments[0]}", headers=headers)
    assert response.status_code == 204
    await _assert_comments(client, root, number, 1, is_pr)
    assert len((await client.get(f"{root}/issues/{number}/comments")).json()) == 1
    # Another issue/PR in this repository must not acquire this count.
    other_number = 2 if is_pr else 1
    if is_pr:
        created = await client.post(root + "/issues", json={"title": "Other"}, headers=headers)
        assert created.status_code == 201
    assert (await client.get(f"{root}/issues/{other_number}")).json()["comments"] == 0


async def test_review_counts_are_separate_from_issue_comments(client, db_session, test_token):
    headers = auth_headers(test_token)
    name = await _create_real_pr_with_diff(client, db_session, test_token)
    root = f"{API}/repos/testuser/{name}"
    async def check(expected):
        for endpoint in [root + "/pulls/1", root + "/pulls"]:
            response = await client.get(endpoint)
            assert response.status_code == 200
            data = response.json()
            if isinstance(data, list):
                data = data[0]
            assert data["review_comments"] == expected
            assert data["comments"] == 1
    response = await client.post(root + "/issues/1/comments", json={"body": "Conversation"}, headers=headers)
    assert response.status_code == 201
    await check(0)
    # Test both comment creation mechanisms, including comments embedded in a review.
    response = await client.post(root + "/pulls/1/reviews", json={"event": "COMMENT", "body": "Summary is not an inline comment", "comments": [{"path": "feature.txt", "line": 1, "body": "Finding"}]}, headers=headers)
    assert response.status_code == 201, response.text
    await check(1)
    response = await client.post(root + "/pulls/1/comments", json={"path": "feature.txt", "line": 1, "body": "Another finding"}, headers=headers)
    assert response.status_code == 201
    await check(2)
    response = await client.delete(f"{root}/pulls/comments/{response.json()['id']}", headers=headers)
    assert response.status_code == 204
    await check(1)
    assert (await client.get(root + "/issues/1")).json()["comments"] == 1


async def test_git_counters_consistent_in_pr_responses(client, db_session, test_token):
    name = await _create_real_pr_with_diff(client, db_session, test_token)
    root = f"{API}/repos/testuser/{name}"
    repository = (await db_session.execute(select(Repository).where(Repository.full_name == f"testuser/{name}"))).scalar_one()
    await write_file(repository.disk_path, "feature", "feature.txt", b"replacement\nsecond line\n", "Revise feature", "Test User", "test@test.com")
    await write_file(repository.disk_path, "feature", "README.md", b"Replaced README\nwith two lines\n", "Replace README", "Test User", "test@test.com")
    expected = {"commits": 3, "additions": 4, "deletions": 1, "changed_files": 2}
    for response in [await client.get(root + "/pulls/1"), await client.patch(root + "/pulls/1", json={"title": "Updated"}, headers=auth_headers(test_token)), await client.post(root + "/pulls", json={"title": "Second PR", "head": "feature", "base": "main"}, headers=auth_headers(test_token))]:
        assert response.status_code in (200, 201), response.text
        data = response.json()
        assert {key: data[key] for key in expected} == expected


async def test_pr_list_omits_git_counters(client, db_session, test_token):
    # GitHub's list returns the simple pull-request shape, without these four,
    # and computing them costs Git subprocesses per row.
    name = await _create_real_pr_with_diff(client, db_session, test_token)
    response = await client.get(f"{API}/repos/testuser/{name}/pulls")
    assert response.status_code == 200, response.text
    data = response.json()[0]
    assert not {"commits", "additions", "deletions", "changed_files"} & data.keys()
    assert data["number"] == 1


async def test_merged_pr_stats_survive_head_branch_deletion(client, db_session, test_token):
    name = await _create_real_pr_with_diff(client, db_session, test_token)
    root = f"{API}/repos/testuser/{name}"
    headers = auth_headers(test_token)
    response = await client.put(root + "/pulls/1/merge", json={}, headers=headers)
    assert response.status_code == 200, response.text
    response = await client.delete(root + "/git/refs/heads/feature", headers=headers)
    assert response.status_code == 204, response.text
    response = await client.get(root + "/pulls/1")
    assert response.status_code == 200
    data = response.json()
    assert {key: data[key] for key in ["commits", "additions", "deletions", "changed_files"]} == {"commits": 1, "additions": 1, "deletions": 0, "changed_files": 1}
