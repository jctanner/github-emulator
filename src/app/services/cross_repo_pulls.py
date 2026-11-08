"""Pull requests whose head branch lives in another repository: a fork.

On GitHub a pull request's head may be a branch of any repository in the
base repository's fork network. The base repository then carries the head
commit as ``refs/pull/{number}/head``, so its diff, files, commits and merge
work from the base repository alone, and a push to the fork's branch moves
the pull request (``synchronize``).

The emulator keeps the same arrangement. ``PullRequest.head_repo_id`` names
the head repository; a same-repository pull request has it unset or equal to
``repo_id``. For a cross-repository one, :func:`sync_head_ref` copies the
fork's branch into the base repository's ``refs/pull/{number}/head`` when the
pull request opens and on every push to that branch.
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pull_request import PullRequest
from app.models.repository import Repository


def pull_ref(number: int) -> str:
    """The base repository's ref for a pull request's head."""
    return f"refs/pull/{number}/head"


def is_cross_repository(pr: PullRequest) -> bool:
    return pr.head_repo_id is not None and pr.head_repo_id != pr.repo_id


def same_repository_head(repo_id: int):
    """SQL condition: a pull request of ``repo_id`` whose head branch is one
    of ``repo_id``'s own. Without it, a push to a base branch that happens to
    share a fork branch's name moved the fork's pull request too."""
    return or_(PullRequest.head_repo_id.is_(None), PullRequest.head_repo_id == repo_id)


async def network_root(db: AsyncSession, repository: Repository) -> int:
    """The id of the repository at the root of ``repository``'s fork network."""
    seen = set()
    current = repository
    while current.parent_id is not None and current.parent_id not in seen:
        seen.add(current.id)
        parent = (
            await db.execute(select(Repository).where(Repository.id == current.parent_id))
        ).scalar_one_or_none()
        if parent is None:
            break
        current = parent
    return current.id


async def same_network(db: AsyncSession, base: Repository, head: Repository) -> bool:
    """Whether a pull request into ``base`` may take its head from ``head``."""
    if base.id == head.id:
        return True
    return await network_root(db, base) == await network_root(db, head)


async def resolve_fork_for_owner(
    db: AsyncSession, base: Repository, owner_login: str, repo_name: str | None = None
) -> Repository | None:
    """The repository of ``owner_login`` in ``base``'s fork network, as REST's
    ``head: "owner:branch"`` (and optional ``head_repo``) names it."""
    query = select(Repository).where(Repository.full_name.like(f"{owner_login}/%"))
    if repo_name:
        query = select(Repository).where(Repository.full_name == f"{owner_login}/{repo_name}")
    candidates = (await db.execute(query.order_by(Repository.id))).scalars().all()
    # A direct fork of the base first, then anything else in the network.
    candidates.sort(key=lambda repo: (repo.parent_id != base.id, repo.id))
    for candidate in candidates:
        if candidate.full_name.split("/", 1)[0] != owner_login:
            continue  # LIKE treats "_" in the login as a wildcard
        if candidate.id != base.id and await same_network(db, base, candidate):
            return candidate
    return None


async def sync_head_ref(
    base: Repository, head: Repository, branch: str, number: int
) -> str | None:
    """Copy ``head``'s ``branch`` into ``base`` as ``refs/pull/{number}/head``.

    Returns the head commit, or None when the branch does not exist or a
    repository has no storage.
    """
    if not (base.disk_path and head.disk_path and os.path.isdir(base.disk_path) and os.path.isdir(head.disk_path)):
        return None
    process = await asyncio.create_subprocess_exec(
        "git", "--git-dir", base.disk_path, "fetch", "--no-tags", "--quiet",
        head.disk_path, f"+refs/heads/{branch}:{pull_ref(number)}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await process.communicate()
    if process.returncode != 0:
        return None
    process = await asyncio.create_subprocess_exec(
        "git", "--git-dir", base.disk_path, "rev-parse", "--verify", "--quiet", pull_ref(number),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await process.communicate()
    sha = out.decode().strip()
    return sha or None
