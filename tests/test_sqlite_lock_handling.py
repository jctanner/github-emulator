"""Regression tests for SQLite writer-lock handling."""

import sqlite3
from contextlib import contextmanager

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.database_retry import commit_with_sqlite_retry
from app.middleware.error_handler import RetryableDatabaseError
from app.models.import_job import ImportJob
from app.models.repository import Repository
from tests.conftest import auth_headers

API = "/api/v3"


class _ReplayableSession:
    def __init__(self, failures: int):
        self.failures = failures
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1
        if self.commits <= self.failures:
            raise OperationalError("COMMIT", {}, Exception("database is locked"))

    async def rollback(self):
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_shared_write_policy_replays_after_one_lock(monkeypatch):
    session = _ReplayableSession(failures=1)
    replay_count = 0

    async def replay():
        nonlocal replay_count
        replay_count += 1

    monkeypatch.setattr("app.database_retry.settings.SQLITE_WRITE_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr("app.database_retry.settings.SQLITE_WRITE_RETRY_DELAY_MS", 0)
    await commit_with_sqlite_retry(session, label="test write", before_retry=replay)

    assert session.commits == 2
    assert session.rollbacks == 1
    assert replay_count == 1


@pytest.mark.asyncio
async def test_shared_write_policy_exhaustion_is_retryable_503(monkeypatch):
    session = _ReplayableSession(failures=2)
    monkeypatch.setattr("app.database_retry.settings.SQLITE_WRITE_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr("app.database_retry.settings.SQLITE_WRITE_RETRY_DELAY_MS", 0)

    with pytest.raises(RetryableDatabaseError):
        await commit_with_sqlite_retry(
            session,
            label="test write",
            before_retry=lambda: None,
        )

    assert session.commits == 2
    assert session.rollbacks == 2


@pytest.mark.asyncio
async def test_shared_write_policy_does_not_retry_without_replay(monkeypatch):
    session = _ReplayableSession(failures=1)
    monkeypatch.setattr("app.database_retry.settings.SQLITE_WRITE_RETRY_ATTEMPTS", 3)
    monkeypatch.setattr("app.database_retry.settings.SQLITE_WRITE_RETRY_DELAY_MS", 0)

    with pytest.raises(RetryableDatabaseError):
        await commit_with_sqlite_retry(session, label="unsafe write")

    assert session.commits == 1
    assert session.rollbacks == 1


@contextmanager
def held_sqlite_writer_lock(db_path):
    con = sqlite3.connect(db_path, timeout=0.1, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("UPDATE users SET login = login WHERE id = 1")
        yield
    finally:
        con.rollback()
        con.close()


@pytest.mark.asyncio
async def test_admin_import_repo_returns_retryable_503_on_sqlite_lock(
    client, db_session, test_user, test_token, tmp_path
):
    """Import job creation should not expose SQLite lock as HTTP 500."""
    with held_sqlite_writer_lock(tmp_path / "test.db"):
        resp = await client.post(
            f"{API}/admin/repos/import",
            json={"url": "https://github.com/octocat/Hello-World", "owner": "testuser"},
            headers=auth_headers(test_token),
        )

    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "1"
    body = resp.json()
    assert body["message"] == "Database is busy, retry the request"
    assert body["errors"] == [{"resource": "Database", "code": "sqlite_locked"}]

    count = (
        await db_session.execute(select(func.count(ImportJob.id)))
    ).scalar_one()
    assert count == 0

    resp = await client.post(
        f"{API}/admin/repos/import",
        json={"url": "https://github.com/octocat/Hello-World", "owner": "testuser"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 202
    assert resp.json()["repo_name"] == "Hello-World"


@pytest.mark.asyncio
async def test_delete_repo_returns_retryable_503_on_sqlite_lock_without_partial_delete(
    client, db_session, test_user, test_token, tmp_path
):
    """Repository delete should rollback cleanly when SQLite remains locked."""
    resp = await client.post(
        f"{API}/user/repos",
        json={"name": "sqlite-lock-delete", "auto_init": True},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201

    with held_sqlite_writer_lock(tmp_path / "test.db"):
        resp = await client.delete(
            f"{API}/repos/testuser/sqlite-lock-delete",
            headers=auth_headers(test_token),
        )

    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "1"
    assert resp.json()["errors"] == [
        {"resource": "Database", "code": "sqlite_locked"}
    ]

    repo = (
        await db_session.execute(
            select(Repository).where(
                Repository.full_name == "testuser/sqlite-lock-delete"
            )
        )
    ).scalar_one_or_none()
    assert repo is not None

    resp = await client.delete(
        f"{API}/repos/testuser/sqlite-lock-delete",
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 204
