"""Record branch refs after application-side Git writes.

Callers commit these updates together with their repository metadata.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.git.bare_repo import get_branches
from app.models.branch import Branch
from app.models.repository import Repository


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
