"""An API commit is all or nothing when SQLite's writer lock is busy.

The contents API and createCommitOnBranch move the git ref, then record the
branch in the database. Under concurrent writers that second write met
"database is locked"; the request answered 503 with the commit already on
the branch, so the client saw a failure for a change already made. Now the
write is replayed, and if the lock persists the ref goes back first.
"""

import base64

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from tests.conftest import API, auth_headers
from tests.test_create_commit_on_branch import _commit, _input, _repo


@pytest.fixture
def locked_commits(monkeypatch):
    """Make the next ``state["fail"]`` commits that write a branch fail locked."""
    state = {"fail": 0, "armed": False}
    real_commit = AsyncSession.commit

    # A write may already be flushed (autoflush before a query), so also
    # remember flushes since the last commit or rollback.
    def flushed(session, _context):
        session.info["wrote"] = True

    def ended(session, *_):
        session.info.pop("wrote", None)

    event.listen(Session, "after_flush", flushed)
    event.listen(Session, "after_commit", ended)
    event.listen(Session, "after_soft_rollback", ended)

    async def commit(self):
        wrote = self.dirty or self.new or self.deleted or self.sync_session.info.get("wrote")
        if state["armed"] and state.get("skip", 0) > 0 and wrote:
            # Let this many writing commits through first.
            state["skip"] -= 1
        elif state["armed"] and state["fail"] > 0 and wrote:
            state["fail"] -= 1
            # What another request does while this one waits on the lock.
            on_fail = state.pop("on_fail", None)
            if on_fail is not None:
                await on_fail()
            raise OperationalError("UPDATE", {}, Exception("database is locked"))
        return await real_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    monkeypatch.setattr("app.database_retry.settings.SQLITE_WRITE_RETRY_ATTEMPTS", 3)
    monkeypatch.setattr("app.database_retry.settings.SQLITE_WRITE_RETRY_DELAY_MS", 0)
    yield state
    event.remove(Session, "after_flush", flushed)
    event.remove(Session, "after_commit", ended)
    event.remove(Session, "after_soft_rollback", ended)


async def _head(client, name):
    return (await client.get(f"{API}/repos/testuser/{name}/branches/main")).json()["commit"]["sha"]


async def _put(client, token, name, sha=None, text=b"two\n"):
    body = {"message": "pr", "content": base64.b64encode(text).decode()}
    if sha:
        body["sha"] = sha
    return await client.put(f"{API}/repos/testuser/{name}/contents/behaviour/pr.txt",
                            json=body, headers=auth_headers(token))


@pytest.mark.asyncio
async def test_contents_put_replays_through_a_brief_lock(client, test_token, locked_commits):
    name, _ = await _repo(client, test_token, "lock-brief")
    created = await _put(client, test_token, name, text=b"one\n")
    assert created.status_code == 201
    sha = created.json()["content"]["sha"]

    locked_commits.update(fail=2, armed=True)
    resp = await _put(client, test_token, name, sha=sha)
    locked_commits["armed"] = False
    assert resp.status_code == 200, resp.text
    assert locked_commits["fail"] == 0
    assert await _head(client, name) == resp.json()["commit"]["sha"]


@pytest.mark.asyncio
async def test_contents_put_lock_that_persists_leaves_nothing_behind(client, test_token, locked_commits):
    name, _ = await _repo(client, test_token, "lock-held")
    created = await _put(client, test_token, name, text=b"one\n")
    sha = created.json()["content"]["sha"]
    before = await _head(client, name)

    locked_commits.update(fail=99, armed=True)
    resp = await _put(client, test_token, name, sha=sha)
    locked_commits["armed"] = False
    assert resp.status_code == 503
    assert await _head(client, name) == before  # the branch went back

    retry = await _put(client, test_token, name, sha=sha)  # the client's retry, same sha
    assert retry.status_code == 200, retry.text


@pytest.mark.asyncio
async def test_create_commit_on_branch_lock_that_persists_moves_the_ref_back(client, test_token, locked_commits):
    name, head = await _repo(client, test_token, "lock-graphql")
    locked_commits.update(fail=99, armed=True)
    first = await _commit(client, test_token, _input(name, head, additions=[("a.txt", b"a\n")]))
    locked_commits["armed"] = False
    assert first.get("errors")
    assert await _head(client, name) == head

    again = await _commit(client, test_token, _input(name, head, additions=[("a.txt", b"a\n")]))
    assert "errors" not in again, again
    assert await _head(client, name) == again["data"]["createCommitOnBranch"]["commit"]["oid"]


@pytest.mark.asyncio
async def test_create_ref_lock_that_persists_removes_the_ref(client, test_token, locked_commits):
    """A behaviour test saw POST git/refs answer 503 with the ref created; the retry got 422."""
    name, head = await _repo(client, test_token, "lock-refs")
    body = {"ref": "refs/heads/feature", "sha": head}
    url = f"{API}/repos/testuser/{name}/git/refs"

    locked_commits.update(fail=99, armed=True)
    resp = await client.post(url, json=body, headers=auth_headers(test_token))
    locked_commits["armed"] = False
    assert resp.status_code == 503
    gone = await client.get(f"{API}/repos/testuser/{name}/git/ref/heads/feature", headers=auth_headers(test_token))
    assert gone.status_code == 404

    retry = await client.post(url, json=body, headers=auth_headers(test_token))
    assert retry.status_code == 201, retry.text


@pytest.mark.asyncio
async def test_update_ref_replays_through_a_brief_lock(client, test_token, locked_commits):
    name, head = await _repo(client, test_token, "lock-refs-update")
    url = f"{API}/repos/testuser/{name}/git/refs"
    await client.post(url, json={"ref": "refs/heads/feature", "sha": head}, headers=auth_headers(test_token))
    put = await _put(client, test_token, name, text=b"one\n")
    new = put.json()["commit"]["sha"]

    locked_commits.update(fail=2, armed=True)
    resp = await client.patch(f"{url}/heads/feature", json={"sha": new}, headers=auth_headers(test_token))
    locked_commits["armed"] = False
    assert resp.status_code == 200, resp.text
    branch = await client.get(f"{API}/repos/testuser/{name}/branches/feature")
    assert branch.json()["commit"]["sha"] == new


@pytest.mark.asyncio
async def test_create_ref_over_an_existing_ref_lock_that_persists_keeps_it(client, test_token, locked_commits):
    """A rollback puts an existing ref back where it was; it does not delete it."""
    name, head = await _repo(client, test_token, "lock-refs-existing")
    url = f"{API}/repos/testuser/{name}/git/refs"
    assert (await client.post(url, json={"ref": "refs/heads/feature", "sha": head},
                              headers=auth_headers(test_token))).status_code == 201
    new = (await _put(client, test_token, name, text=b"one\n")).json()["commit"]["sha"]

    locked_commits.update(fail=99, armed=True)
    resp = await client.post(url, json={"ref": "refs/heads/feature", "sha": new}, headers=auth_headers(test_token))
    locked_commits["armed"] = False
    # 503 here; 422 "Reference already exists" where creates refuse an existing ref.
    assert resp.status_code in (503, 422), resp.text
    kept = await client.get(f"{API}/repos/testuser/{name}/git/ref/heads/feature", headers=auth_headers(test_token))
    assert kept.status_code == 200
    assert kept.json()["object"]["sha"] == head


async def _git_in(disk_path, *args):
    import asyncio

    proc = await asyncio.create_subprocess_exec("git", *args, cwd=disk_path)
    assert await proc.wait() == 0


async def _branch_row_sha(db_session, name, branch):
    from sqlalchemy import select

    from app.models.branch import Branch
    from app.models.repository import Repository

    db_session.expire_all()
    repo = (await db_session.execute(select(Repository).where(Repository.name == name))).scalar_one()
    row = (await db_session.execute(
        select(Branch).where(Branch.repo_id == repo.id, Branch.name == branch)
    )).scalar_one_or_none()
    return repo.disk_path, (row.sha if row else None)


@pytest.mark.asyncio
async def test_update_ref_replay_does_not_record_over_a_newer_move(client, test_token, db_session, locked_commits):
    """Another writer moves the ref during the lock wait; the replay leaves it to them."""
    name, head = await _repo(client, test_token, "lock-refs-newer")
    url = f"{API}/repos/testuser/{name}/git/refs"
    assert (await client.post(url, json={"ref": "refs/heads/feature", "sha": head},
                              headers=auth_headers(test_token))).status_code == 201
    first = (await _put(client, test_token, name, text=b"one\n")).json()["commit"]["sha"]
    second = (await _put(client, test_token, name, text=b"two\n")).json()["commit"]["sha"]
    assert second != first
    disk_path, _ = await _branch_row_sha(db_session, name, "feature")

    async def other_writer():
        await _git_in(disk_path, "update-ref", "refs/heads/feature", second)

    locked_commits.update(fail=1, armed=True, on_fail=other_writer)
    resp = await client.patch(f"{url}/heads/feature", json={"sha": first}, headers=auth_headers(test_token))
    locked_commits["armed"] = False
    assert resp.status_code == 200, resp.text
    _, recorded = await _branch_row_sha(db_session, name, "feature")
    assert recorded != first


@pytest.mark.asyncio
async def test_create_ref_shorthand_lock_that_persists_removes_the_ref(client, test_token, db_session, locked_commits):
    """POST git/refs with "heads/x": the rollback finds the ref it created."""
    name, head = await _repo(client, test_token, "lock-refs-short")
    url = f"{API}/repos/testuser/{name}/git/refs"

    locked_commits.update(fail=99, armed=True)
    resp = await client.post(url, json={"ref": "heads/short", "sha": head}, headers=auth_headers(test_token))
    locked_commits["armed"] = False
    assert resp.status_code == 503
    gone = await client.get(f"{API}/repos/testuser/{name}/git/ref/heads/short", headers=auth_headers(test_token))
    assert gone.status_code == 404
    # Nothing is left on disk under either spelling.
    from app.services.git_service import ref_sha

    disk_path, _ = await _branch_row_sha(db_session, name, "short")
    assert await ref_sha(disk_path, "heads/short") is None
    assert await ref_sha(disk_path, "refs/heads/short") is None


@pytest.mark.asyncio
async def test_contents_put_replay_does_not_record_over_a_newer_move(client, test_token, db_session, locked_commits):
    """The contents path follows the same rule as git/refs."""
    name, head = await _repo(client, test_token, "lock-put-newer")
    disk_path, _ = await _branch_row_sha(db_session, name, "main")

    async def other_writer():
        # Another request moves main back to the head it started from.
        await _git_in(disk_path, "update-ref", "refs/heads/main", head)

    locked_commits.update(fail=1, armed=True, on_fail=other_writer)
    resp = await _put(client, test_token, name, text=b"three\n")
    locked_commits["armed"] = False
    assert resp.status_code in (200, 201), resp.text
    _, recorded = await _branch_row_sha(db_session, name, "main")
    assert recorded != resp.json()["commit"]["sha"]


@pytest.mark.asyncio
async def test_pull_request_heads_replay_through_a_brief_lock(
    client, test_token, test_user, db_session, locked_commits, monkeypatch
):
    """after_api_commit's head update commits through the shared retry."""
    from sqlalchemy import select

    from app.models.pull_request import PullRequest
    from app.models.repository import Repository
    from app.services import api_push, workflow_service

    name, head = await _repo(client, test_token, "lock-pr-heads")
    h = auth_headers(test_token)
    url = f"{API}/repos/testuser/{name}"
    assert (await client.post(f"{url}/git/refs", json={"ref": "refs/heads/topic", "sha": head}, headers=h)).status_code == 201
    body = {"message": "t", "content": base64.b64encode(b"t\n").decode(), "branch": "topic"}
    moved = (await client.put(f"{url}/contents/t.txt", json=body, headers=h)).json()["commit"]["sha"]
    pr = await client.post(f"{url}/pulls", json={"title": "p", "head": "topic", "base": "main"}, headers=h)
    assert pr.status_code == 201, pr.text

    async def no_push_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(workflow_service, "process_push_event", no_push_event)
    repository = (await db_session.execute(select(Repository).where(Repository.name == name))).scalar_one()
    repo_id = repository.id
    row = (await db_session.execute(select(PullRequest).where(PullRequest.repo_id == repository.id))).scalar_one()
    row.head_sha = head
    await db_session.commit()

    locked_commits.update(fail=1, armed=True)
    await api_push.after_api_commit(db_session, repository, test_user, "topic", head, moved)
    locked_commits["armed"] = False
    db_session.expire_all()
    row = (await db_session.execute(select(PullRequest).where(PullRequest.repo_id == repo_id))).scalar_one()
    assert row.head_sha == moved


async def _open_pull_request(client, test_token, name, head):
    """A topic branch at ``head`` with one commit, and an open pull request from it."""
    h = auth_headers(test_token)
    url = f"{API}/repos/testuser/{name}"
    assert (await client.post(f"{url}/git/refs", json={"ref": "refs/heads/topic", "sha": head}, headers=h)).status_code == 201
    body = {"message": "t", "content": base64.b64encode(b"t\n").decode(), "branch": "topic"}
    first = (await client.put(f"{url}/contents/t.txt", json=body, headers=h)).json()["commit"]["sha"]
    pr = await client.post(f"{url}/pulls", json={"title": "p", "head": "topic", "base": "main"}, headers=h)
    assert pr.status_code == 201, pr.text
    return url, first, pr.json()["number"]


@pytest.mark.asyncio
async def test_an_obsolete_api_commit_leaves_newer_pull_request_heads(client, test_token, db_session, locked_commits):
    """The branch moves on during the record's lock wait: neither the branch
    record nor the pull request's head goes back to the older commit."""
    name, head = await _repo(client, test_token, "lock-pr-newer")
    url, first, number = await _open_pull_request(client, test_token, name, head)
    disk_path, _ = await _branch_row_sha(db_session, name, "topic")

    async def other_writer():
        # Another request moves topic back to ``first`` (its own newer push).
        await _git_in(disk_path, "update-ref", "refs/heads/topic", first)

    body = {"message": "u", "content": base64.b64encode(b"u\n").decode(), "branch": "topic",
            "sha": (await client.get(f"{url}/contents/t.txt?ref=topic", headers=auth_headers(test_token))).json()["sha"]}
    locked_commits.update(fail=1, armed=True, on_fail=other_writer)
    resp = await client.put(f"{url}/contents/t.txt", json=body, headers=auth_headers(test_token))
    locked_commits["armed"] = False
    assert resp.status_code == 200, resp.text
    obsolete = resp.json()["commit"]["sha"]

    _, recorded = await _branch_row_sha(db_session, name, "topic")
    assert recorded != obsolete
    from sqlalchemy import select

    from app.models.pull_request import PullRequest

    db_session.expire_all()
    head_sha = (await db_session.execute(select(PullRequest.head_sha).where(PullRequest.head_ref == "topic"))).scalar_one()
    assert head_sha != obsolete


@pytest.mark.asyncio
async def test_a_failed_push_event_still_answers_the_api_commit(client, test_token, monkeypatch):
    """A dispatch failure after the commit rolls back; the request still succeeds."""
    from app.services import workflow_service

    name, head = await _repo(client, test_token, "push-event-fails")
    url, _first, number = await _open_pull_request(client, test_token, name, head)

    async def failing_push_event(*_args, **_kwargs):
        raise RuntimeError("dispatch failed")

    monkeypatch.setattr(workflow_service, "process_push_event", failing_push_event)
    sha = (await client.get(f"{url}/contents/t.txt?ref=topic", headers=auth_headers(test_token))).json()["sha"]
    body = {"message": "v", "content": base64.b64encode(b"v\n").decode(), "branch": "topic", "sha": sha}
    resp = await client.put(f"{url}/contents/t.txt", json=body, headers=auth_headers(test_token))
    assert resp.status_code == 200, resp.text
    pull = (await client.get(f"{url}/pulls/{number}", headers=auth_headers(test_token))).json()
    assert pull["head"]["sha"] == resp.json()["commit"]["sha"]


@pytest.mark.asyncio
async def test_a_lock_on_the_head_update_does_not_fail_the_committed_write(
    client, test_token, db_session, locked_commits, monkeypatch
):
    """The contents commit and branch record are in; a lock that persists on
    the pull request heads afterwards leaves them stale but answers 200."""
    from sqlalchemy import select

    from app.models.pull_request import PullRequest
    from app.services import workflow_service

    name, head = await _repo(client, test_token, "lock-heads-exhausted")
    url, first, _number = await _open_pull_request(client, test_token, name, head)

    async def no_push_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(workflow_service, "process_push_event", no_push_event)
    sha = (await client.get(f"{url}/contents/t.txt?ref=topic", headers=auth_headers(test_token))).json()["sha"]
    body = {"message": "w", "content": base64.b64encode(b"w\n").decode(), "branch": "topic", "sha": sha}
    locked_commits.update(skip=1, fail=99, armed=True)
    resp = await client.put(f"{url}/contents/t.txt", json=body, headers=auth_headers(test_token))
    locked_commits["armed"] = False
    assert resp.status_code == 200, resp.text
    moved = resp.json()["commit"]["sha"]

    _, recorded = await _branch_row_sha(db_session, name, "topic")
    assert recorded == moved
    db_session.expire_all()
    head_sha = (await db_session.execute(select(PullRequest.head_sha).where(PullRequest.head_ref == "topic"))).scalar_one()
    assert head_sha == first


@pytest.mark.asyncio
async def test_a_ref_moved_before_the_first_attempt_is_not_recorded(client, test_token, db_session):
    """No lock error needed: a writer that moved the ref and recorded it first keeps its value."""
    from sqlalchemy import select

    from app.models.repository import Repository
    from app.services.branch_service import commit_if_ref_unmoved

    name, head = await _repo(client, test_token, "unmoved-first")
    # Another writer moves main on from ``head``.
    assert (await _put(client, test_token, name, text=b"one\n")).status_code in (200, 201)
    repository = (await db_session.execute(select(Repository).where(Repository.name == name))).scalar_one()
    # This request moved main to ``head``; another moved it on and
    # recorded that before this request's first database attempt.
    staged = []

    async def stage():
        staged.append(True)

    committed = await commit_if_ref_unmoved(
        db_session, repository_id=repository.id, disk_path=repository.disk_path, ref="main",
        after=head, stage=stage, label="test",
    )
    assert committed is False and staged == []


@pytest.mark.asyncio
async def test_an_obsolete_commit_still_raises_its_push(client, test_token, test_user, db_session, monkeypatch):
    """The push event is the commit's own; only the pull request heads are left to the newer push."""
    from sqlalchemy import select

    from app.models.repository import Repository
    from app.services import api_push, workflow_service

    name, head = await _repo(client, test_token, "obsolete-push")
    first = (await _put(client, test_token, name, text=b"one\n")).json()["commit"]["sha"]
    calls = []

    async def recording(*_args, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(workflow_service, "process_push_event", recording)
    repository = (await db_session.execute(select(Repository).where(Repository.name == name))).scalar_one()
    # main is at ``first``; report the older move head~ -> head as this request's.
    await api_push.after_api_commit(db_session, repository, test_user, "main", None, head)
    assert len(calls) == 1
    assert calls[0]["after_sha"] == head and calls[0]["update_heads"] is False
    assert first != head


@pytest.mark.asyncio
async def test_api_ref_writes_to_one_repository_run_one_at_a_time(client, test_token, db_session, monkeypatch):
    """A second contents write waits until the first has recorded its ref,
    moved the pull request heads and raised its push: the record and the
    heads end at the later commit."""
    import asyncio

    from sqlalchemy import select

    from app.models.pull_request import PullRequest
    from app.services import api_push

    name, head = await _repo(client, test_token, "serial-writes")
    url, _first, _number = await _open_pull_request(client, test_token, name, head)
    disk_path, _ = await _branch_row_sha(db_session, name, "topic")
    h = auth_headers(test_token)

    paused, release = asyncio.Event(), asyncio.Event()
    real_after = api_push.after_api_commit

    async def slow_after(db, repository, user, branch, before, after):
        if not paused.is_set():
            paused.set()
            await release.wait()
        return await real_after(db, repository, user, branch, before, after)

    monkeypatch.setattr("app.api.contents.after_api_commit", slow_after)

    async def put(path, text):
        body = {"message": path, "content": base64.b64encode(text).decode(), "branch": "topic"}
        return await client.put(f"{url}/contents/{path}", json=body, headers=h)

    first = asyncio.create_task(put("a.txt", b"a\\n"))
    await paused.wait()
    at_pause = await _ref_at(disk_path, "refs/heads/topic")
    second = asyncio.create_task(put("b.txt", b"b\\n"))
    await asyncio.sleep(0.3)
    # The second write has not moved the ref while the first is still running.
    assert await _ref_at(disk_path, "refs/heads/topic") == at_pause
    release.set()
    a, b = await first, await second
    assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
    later = b.json()["commit"]["sha"]

    _, recorded = await _branch_row_sha(db_session, name, "topic")
    assert recorded == later
    db_session.expire_all()
    head_sha = (await db_session.execute(select(PullRequest.head_sha).where(PullRequest.head_ref == "topic"))).scalar_one()
    assert head_sha == later


async def _ref_at(disk_path, ref):
    from app.services.git_service import ref_sha

    return await ref_sha(disk_path, ref)
