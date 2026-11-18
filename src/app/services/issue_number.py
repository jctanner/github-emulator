"""Allocate an issue or pull request number atomically."""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.models.repository import Repository


async def allocate_issue_number(db: AsyncSession, repository: Repository, *, opened: bool = True) -> int:
    """Take the repository's next issue number, and count the issue as open.

    One UPDATE ... RETURNING, so two requests creating issues at once get
    different numbers. Reading ``next_issue_number`` from the loaded row and
    writing it back gave both the same number when the second read before
    the first committed, and the second failed with a unique-constraint 500
    (and ``open_issues_count`` lost an increment).
    """
    values = {"next_issue_number": Repository.next_issue_number + 1}
    if opened:
        values["open_issues_count"] = Repository.open_issues_count + 1
    row = (await db.execute(
        update(Repository)
        .where(Repository.id == repository.id)
        .values(**values)
        .returning(Repository.next_issue_number, Repository.open_issues_count)
        .execution_options(synchronize_session=False)
    )).one()
    # Keep the loaded object in step, without marking it changed: flushing a
    # stale value back would undo another request's increment.
    set_committed_value(repository, "next_issue_number", row.next_issue_number)
    set_committed_value(repository, "open_issues_count", row.open_issues_count)
    return row.next_issue_number - 1
