"""What a workflow run costs inside SQLite's write lock.

SQLite has one writer: from a transaction's first write to its commit every
other writer waits. Creating a run (on an issue, pull request or comment)
and promoting its jobs (when a job finishes) did their reads inside that
window, and the reads were heavy: loading a Repository or User loaded every
issue, label, branch and repository behind it. Under Fullsend's behaviour
tests that held the lock for 2-13 s per event. These pin the reads outside
the write and the write itself to the rows it changes.
"""

import base64

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.actions import WorkflowJob
from app.services.workflow_service import check_run_completion, dispatch_ready_jobs
from tests.conftest import API, auth_headers

SHIM = """
name: shim
on:
  issues:
    types: [opened, edited, labeled]
jobs:
  dispatch:
    if: github.event_name != 'issue_comment'
    permissions:
      contents: write
      issues: write
    uses: ./.github/workflows/dispatch.yml
    with:
      event_action: ${{ github.event.action }}
      mint_url: ${{ vars.MINT }}
    secrets:
      A: ${{ secrets.A }}
"""


def dispatch_yaml(n):
    jobs = ["""  route:
    runs-on: ubuntu-latest
    outputs:
      stage: ${{ steps.r.outputs.stage }}
    steps:
      - id: r
        run: echo stage=triage >> "$GITHUB_OUTPUT"
"""]
    for i in range(n):
        jobs.append(f"""  stage{i}:
    needs: [route]
    if: needs.route.outputs.stage == 'triage'
    runs-on: ubuntu-latest
    concurrency:
      group: s{i}-${{{{ github.event.issue.number }}}}
      cancel-in-progress: true
    steps:
      - run: echo {i}
""")
    return """
name: dispatch
on:
  workflow_call:
    inputs:
      event_action: {type: string}
      mint_url: {type: string}
    secrets:
      A: {required: false}
jobs:
""" + "".join(jobs)


async def _setup(client, test_token, issues=1):
    h = auth_headers(test_token)
    assert (await client.post(f"{API}/user/repos", json={"name": "p", "auto_init": True}, headers=h)).status_code == 201
    for path, text in ((".github/workflows/shim.yml", SHIM), (".github/workflows/dispatch.yml", dispatch_yaml(12))):
        r = await client.put(f"{API}/repos/testuser/p/contents/{path}", headers=h,
                             json={"message": "wf", "content": base64.b64encode(text.encode()).decode()})
        assert r.status_code in (200, 201), r.text
    return h


class _Writes:
    """Statements from each transaction's first write to its commit."""

    def __init__(self, engine):
        self.engine = engine.sync_engine
        self.transactions = []
        self._current = None

    def _before(self, conn, cursor, statement, parameters, context, executemany):
        words = statement.split()
        if self._current is None and words[0].upper() in ("INSERT", "UPDATE", "DELETE"):
            self._current = []
        if self._current is not None:
            self._current.append(" ".join(words[:4]))

    def _commit(self, conn):
        if self._current is not None:
            self.transactions.append(self._current)
            self._current = None

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._before)
        event.listen(self.engine, "commit", self._commit)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._before)
        event.remove(self.engine, "commit", self._commit)


@pytest.mark.asyncio
async def test_creating_a_run_reads_nothing_inside_the_write(client, db_engine, test_token):
    h = await _setup(client, test_token)
    with _Writes(db_engine) as writes:
        assert (await client.post(f"{API}/repos/testuser/p/issues", json={"title": "t"}, headers=h)).status_code == 201
    run_tx = next(tx for tx in writes.transactions if any("workflow_runs" in s for s in tx))
    # One run and its 13 jobs; was 64 statements, 50 of them reads.
    assert [s for s in run_tx if not s.upper().startswith("INSERT")] == []
    assert len(run_tx) == 14


@pytest.mark.asyncio
async def test_promoting_jobs_writes_in_one_pass(client, db_engine, db_session, test_token):
    h = await _setup(client, test_token)
    for i in range(3):
        assert (await client.post(f"{API}/repos/testuser/p/issues", json={"title": f"t{i}"}, headers=h)).status_code == 201
    async with AsyncSession(db_engine) as s:
        job = (await s.execute(
            select(WorkflowJob).where(WorkflowJob.job_key.like("%route")).order_by(WorkflowJob.id.desc())
        )).scalars().first()
        run_id = job.run_id
        job.status, job.conclusion, job.outputs = "completed", "success", {"stage": "triage"}
        await s.commit()
    with _Writes(db_engine) as writes:
        async with AsyncSession(db_engine) as s:
            await dispatch_ready_jobs(s, run_id)
            await check_run_completion(s, run_id)
            await s.commit()
    (tx,) = writes.transactions
    # 12 jobs promoted, each superseding the previous issues' runs in its
    # group: was 68 statements (a supersede query and two UPDATEs per job,
    # and every issue of the repository reloaded); now the batched job
    # UPDATE, the run's completion check and its UPDATE.
    assert not any(t in s for s in tx for t in (" issues", " repositories", " labels", " branches"))
    assert len(tx) <= 15, tx


@pytest.mark.asyncio
async def test_reindexing_writes_one_statement_per_table(client, db_engine, test_token):
    from app.models.repository import Repository
    from app.services.index_service import index_repository

    h = auth_headers(test_token)
    assert (await client.post(f"{API}/user/repos", json={"name": "ix", "auto_init": True}, headers=h)).status_code == 201
    for i in range(10):
        r = await client.put(f"{API}/repos/testuser/ix/contents/f{i}.txt", headers=h,
                             json={"message": f"c{i}", "content": base64.b64encode(b"x").decode()})
        assert r.status_code == 201
    with _Writes(db_engine) as writes:
        async with AsyncSession(db_engine) as s:
            repo = (await s.execute(select(Repository).where(Repository.name == "ix"))).scalar_one()
            assert await index_repository(s, repo) == 22
    # Was one INSERT per file and per commit (a push indexing a real
    # repository ran ~1000 statements inside the write).
    assert [" ".join(s.split()[:3]) for s in writes.transactions[-1]] == [
        "DELETE FROM file_contents", "DELETE FROM commit_metadata",
        "INSERT INTO file_contents", "INSERT INTO commit_metadata",
    ]
