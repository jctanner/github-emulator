"""A failed post-push step must not take the later steps down with it.

Each step swallowed its error with no rollback. A failed flush leaves the
session unusable until it is rolled back (on PostgreSQL any failed statement
aborts the transaction), so the push event after it failed as well and the
pull request's synchronize, with the workflows it starts, never happened.
"""

import logging

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.label import Label
from app.models.repository import Repository
from tests.conftest import API, auth_headers


@pytest.mark.asyncio
async def test_the_push_event_runs_after_a_failed_step(client, test_user, test_token, db_session, db_engine, monkeypatch, caplog):
    import app.database as database
    import app.services.index_service as index_service
    import app.services.workflow_service as workflow_service
    from app.git import smart_http

    monkeypatch.setattr(database, "async_session", async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False))
    resp = await client.post(f"{API}/user/repos", json={"name": "post-push", "auto_init": True}, headers=auth_headers(test_token))
    assert resp.status_code == 201, resp.text
    resp = await client.post(f"{API}/repos/testuser/post-push/labels", json={"name": "dup", "color": "000000"},
                             headers=auth_headers(test_token))
    assert resp.status_code == 201, resp.text
    repository = (await db_session.execute(select(Repository).where(Repository.full_name == "testuser/post-push"))).scalar_one()

    async def failing_index(db, repo):
        db.add(Label(repo_id=repo.id, name="dup", color="ffffff"))
        await db.flush()  # unique (repo_id, name): IntegrityError

    seen = []

    async def recording_push_event(db, repo, user, **kwargs):
        # The session must be usable here: the old code raised PendingRollbackError.
        seen.append((await db.execute(select(Repository.full_name).where(Repository.id == repo.id))).scalar_one())

    monkeypatch.setattr(index_service, "index_repository", failing_index)
    monkeypatch.setattr(workflow_service, "process_push_event", recording_push_event)

    with caplog.at_level(logging.ERROR, logger="app.git.smart_http"):
        await smart_http._post_receive_pack_tasks(repository.id, test_user.id)

    assert seen == ["testuser/post-push"]
    assert any("index repository" in r.getMessage() for r in caplog.records)
