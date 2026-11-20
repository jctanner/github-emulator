"""Record branch refs after application-side Git writes.

Callers commit these updates together with their repository metadata.
"""

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database_retry import commit_with_sqlite_retry
from app.git.bare_repo import get_branches
from app.middleware.error_handler import RetryableDatabaseError
from app.models.branch import Branch
from app.models.repository import Repository
from app.services.git_service import full_ref, move_ref_back, ref_sha


async def record_branch(
    db: AsyncSession, repository: Repository, name: str, sha: str,
) -> None:
    """Insert or advance a branch without replacing its protection settings."""
    branch = (await db.execute(
        select(Branch).where(Branch.repo_id == repository.id, Branch.name == name)
    )).scalar_one_or_none()
    if branch is None:
        db.add(Branch(repo_id=repository.id, name=name, sha=sha))
    else:
        branch.sha = sha


async def record_disk_branches(db: AsyncSession, repository: Repository) -> None:
    """Register all actual heads after cloning a repository, including packed refs."""
    for branch in await get_branches(repository.disk_path):
        await record_branch(db, repository, branch["name"], branch["sha"])


async def commit_if_ref_unmoved(
    db: AsyncSession,
    *,
    repository_id: int,
    disk_path: str,
    ref: str,
    after: str,
    stage,
    label: str,
    refresh=None,
) -> bool:
    """Commit a moved ref's database record, unless another writer moved it on.

    The ref already points at ``after`` on disk. Each attempt first takes the
    repository's write lock (a no-op UPDATE of its row: SQLite's writer lock,
    PostgreSQL's row lock), then reads the ref. Every writer of this record
    takes that lock before it commits, so a writer that moved the ref later
    has either committed already (the ref has moved: nothing is staged, and
    its value stays) or waits until this commit is done and writes over it.
    So the recorded value never goes back to an older sha, whatever the order
    of the git moves and the commits. ``stage`` makes the changes uncommitted;
    on a lock, ``commit_with_sqlite_retry`` rolls back, calls ``refresh`` and
    tries again.

    Returns whether this request's value was staged and committed. Raises
    RetryableDatabaseError if the lock persists.
    """
    staged = False

    async def lock_and_stage() -> None:
        nonlocal staged
        await db.execute(
            update(Repository).where(Repository.id == repository_id).values(id=Repository.id)
            .execution_options(synchronize_session=False)
        )
        staged = await ref_sha(disk_path, full_ref(ref)) == after
        if staged:
            await stage()

    await commit_with_sqlite_retry(db, label=label, before_commit=lock_and_stage, before_retry=refresh)
    return staged


async def record_moved_branch(
    db: AsyncSession,
    repository: Repository,
    branch: str,
    before: str | None,
    after: str,
    *,
    refresh: tuple = (),
) -> Repository:
    """Record a branch an API write has just moved on disk, as one request.

    The git ref moves first and the database second. Under concurrent
    writers SQLite refuses that second write ("database is locked"), and
    answering 503 then left the commit on the branch, so the client's retry
    wrote it twice or met a conflict. So: end the read transaction, write in
    a fresh one through ``commit_if_ref_unmoved`` (which also keeps an older
    sha from being recorded over a newer one), replaying it on a lock, and if
    the lock persists put the branch back where it was before the 503. That
    is only done while the branch is still at ``after``: if another writer
    has already moved it past ``after``, the commit stays.

    ``refresh`` lists other loaded objects the caller reads afterwards; a
    rollback expires them, so they are reloaded on replay. Returns the
    repository, reloaded if a replay happened.
    """
    repository_id, disk_path = repository.id, repository.disk_path
    current = repository
    await db.commit()  # nothing pending: ends the read snapshot

    async def reload() -> None:
        nonlocal current
        current = await db.get(Repository, repository_id, populate_existing=True)
        for obj in refresh:
            await db.refresh(obj)

    async def stage() -> None:
        await record_branch(db, current, branch, after)
        current.pushed_at = datetime.now(timezone.utc)

    try:
        await commit_if_ref_unmoved(
            db, repository_id=repository_id, disk_path=disk_path, ref=branch, after=after,
            stage=stage, label=f"record {branch}", refresh=reload,
        )
    except RetryableDatabaseError:
        await move_ref_back(disk_path, full_ref(branch), before, after)
        raise
    return current


async def replay_ref_record(
    db: AsyncSession,
    repository_id: int,
    disk_path: str,
    ref: str,
    before: str | None,
    after: str,
    stage,
    *,
    refresh: tuple = (),
) -> None:
    """Commit a moved ref's database record through SQLite's writer lock.

    For a route that has already moved ``ref`` on disk from ``before`` to
    ``after``. ``stage`` makes the database changes without committing;
    ``commit_if_ref_unmoved`` commits them, never over a newer value, and on
    a lock rolls back, reloads ``refresh`` and stages again. If the lock
    persists, the ref is put back (only while still at ``after``) before the
    503, so the client's retry finds the repository as it was.
    """
    async def reload() -> None:
        for obj in refresh:
            await db.refresh(obj)

    try:
        await commit_if_ref_unmoved(
            db, repository_id=repository_id, disk_path=disk_path, ref=ref, after=after,
            stage=stage, label=f"record {ref}", refresh=reload,
        )
    except RetryableDatabaseError:
        await move_ref_back(disk_path, full_ref(ref), before, after)
        raise
