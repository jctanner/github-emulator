"""dispatch_event reads git before its first write.

SQLite has one writer; from a transaction's first flush to its commit every
other writer waits. Dispatching an event reads the workflow files and the
reusable workflows a matched workflow calls from git, so those reads come
before the workflow-inventory sync and the run inserts write anything.
"""

import base64

import pytest
from sqlalchemy import event, select

from app.models.repository import Repository
from app.services import workflow_service
from tests.conftest import API, auth_headers

pytestmark = pytest.mark.asyncio

WORKFLOW = "name: ci\non: [push]\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ci\n"


async def test_reusable_workflows_are_read_before_the_first_write(client, test_token, test_user, db_session, monkeypatch):
    created = await client.post(
        f"{API}/user/repos", json={"name": "read-first", "auto_init": True}, headers=auth_headers(test_token)
    )
    assert created.status_code == 201, created.text
    put = await client.put(
        f"{API}/repos/testuser/read-first/contents/.github/workflows/ci.yml",
        json={"message": "ci", "content": base64.b64encode(WORKFLOW.encode()).decode()},
        headers=auth_headers(test_token),
    )
    assert put.status_code in (200, 201), put.text
    repository = (await db_session.execute(select(Repository).where(Repository.name == "read-first"))).scalar_one()
    await db_session.commit()

    flushes = []

    def flushed(session, _context):
        flushes.append(True)

    seen_before_write = []
    materialize = workflow_service.materialize_reusable_workflows

    async def recording(*args, **kwargs):
        seen_before_write.append(not flushes)
        return await materialize(*args, **kwargs)

    monkeypatch.setattr(workflow_service, "materialize_reusable_workflows", recording)
    event.listen(db_session.sync_session, "after_flush", flushed)
    try:
        runs = await workflow_service.dispatch_event(
            db_session, repository, test_user, "push", None, {}, ref="main",
        )
    finally:
        event.remove(db_session.sync_session, "after_flush", flushed)

    assert runs, "the push workflow should have matched"
    assert seen_before_write == [True]
    assert flushes, "the inventory sync and the run are written after the reads"
