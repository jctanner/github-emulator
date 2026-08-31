"""Tests for commit detail responses."""

import pytest
from sqlalchemy import select

from app.git.bare_repo import write_file
from app.models.repository import Repository
from tests.conftest import auth_headers

API = "/api/v3"


@pytest.mark.asyncio
async def test_get_commit_includes_diff_files_and_stats(
    client, db_session, test_token
):
    response = await client.post(
        f"{API}/user/repos",
        json={"name": "commit-diff", "auto_init": True},
        headers=auth_headers(test_token),
    )
    assert response.status_code == 201

    result = await db_session.execute(
        select(Repository).where(Repository.full_name == "testuser/commit-diff")
    )
    repository = result.scalar_one()
    sha = await write_file(
        repository.disk_path,
        "main",
        "example.txt",
        b"a changed line\n",
        "Add an example",
        "Test User",
        "test@test.com",
    )

    response = await client.get(
        f"{API}/repos/testuser/commit-diff/commits/{sha}"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["stats"] == {"total": 1, "additions": 1, "deletions": 0}
    assert len(data["files"]) == 1
    assert data["files"][0]["filename"] == "example.txt"
    assert data["files"][0]["status"] == "added"
    assert data["files"][0]["additions"] == 1
    assert "+a changed line" in data["files"][0]["patch"]
