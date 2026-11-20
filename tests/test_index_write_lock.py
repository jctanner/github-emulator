"""Indexing a pushed repository does not hold SQLite's write lock while it reads.

The indexer used to delete the old index first and then read every file
with a git subprocess each, holding the database's one write lock
throughout. After a large push, every other write in that window waited
past the busy timeout and failed, served as 503 "Database is busy".
"""

import pytest
from sqlalchemy import select, text

from app.models.repository import Repository
from app.models.search_index import FileContent
from app.services import index_service
from tests.conftest import API, auth_headers


@pytest.mark.asyncio
async def test_other_writers_are_not_blocked_while_the_tree_is_read(
    client, db_session, db_engine, test_token, monkeypatch
):
    resp = await client.post(
        f"{API}/user/repos", json={"name": "indexed", "auto_init": True},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201
    repo = (await db_session.execute(
        select(Repository).where(Repository.full_name == "testuser/indexed")
    )).scalar_one()

    real_read = index_service.get_file_content
    writes = []

    async def read_while_someone_else_writes(disk_path, ref, path):
        # Another connection, with a short busy timeout, writes while the
        # indexer is reading. It only succeeds if no write lock is held.
        async with db_engine.connect() as other:
            await other.execute(text("PRAGMA busy_timeout=200"))
            await other.execute(
                text("UPDATE repositories SET description = :d WHERE id = :id"),
                {"d": f"written during read of {path}", "id": repo.id},
            )
            await other.commit()
        writes.append(path)
        return await real_read(disk_path, ref, path)

    monkeypatch.setattr(index_service, "get_file_content", read_while_someone_else_writes)

    # A write the caller still has open, like the branch sync before indexing.
    repo.pushed_at = None
    await db_session.flush()

    count = await index_service.index_repository(db_session, repo)

    assert writes, "the indexer read no file"
    assert count >= 1
    indexed = (await db_session.execute(
        select(FileContent.file_path).where(FileContent.repo_id == repo.id)
    )).scalars().all()
    assert "README.md" in indexed
