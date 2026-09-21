"""Who may read a repository.

A private repository's visibility was checked in exactly one place, inline in
``GET /repos/{owner}/{repo}``, and every other endpoint that resolves a
repository by name missed it. That was wrong in both directions at once:

- a private repository's issues, contents, commits and the rest were served to
  any authenticated token, so "private" meant nothing past the first endpoint;
  and
- the one endpoint that did check tested *ownership*, so a collaborator on a
  private repository was refused by the only door that was locked.

The check lives here so there is one answer to the question, and it is applied
at the authentication chokepoint in ``app.api.deps`` rather than at 165 route
handlers, which is the same placement the job-token permission check already
uses.

A refusal is 404, not 403. GitHub does not confirm that a private repository
exists to someone who cannot see it, and returning 403 would leak exactly the
fact the check is meant to hide.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_loaders import repository_identity_options
from app.models.organization import Organization, OrgMembership
from app.models.repository import Collaborator, Repository
from app.models.user import User


# ``/repos/{owner}/{repo}`` with or without the API prefix. Anything that does
# not name a repository is not this module's business.
_REPO_PATH_RE = re.compile(
    r"^/(?:api/v3/)?repos/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?(?:/.*)?$"
)


def repository_from_path(path: str) -> tuple[str, str] | None:
    """Return ``(owner, repo)`` when a path addresses a repository."""
    match = _REPO_PATH_RE.match(path.split("?", 1)[0])
    if match is None:
        return None
    return match.group("owner"), match.group("repo")


async def can_read(
    db: AsyncSession, repository: Repository, user: User | None
) -> bool:
    """Whether ``user`` may see ``repository`` at all."""
    if not repository.private:
        return True
    if user is None:
        return False
    if user.site_admin or user.id == repository.owner_id:
        return True

    collaborator = (
        await db.execute(
            select(Collaborator.id).where(
                Collaborator.repo_id == repository.id,
                Collaborator.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if collaborator is not None:
        return True

    # A member of the owning organisation can see its private repositories.
    # Without this, an organisation's own members are locked out of every
    # repository they have not been added to individually, which is not how
    # either GitHub or this emulator's seeding behaves.
    if repository.owner_type == "Organization":
        membership = (
            await db.execute(
                select(OrgMembership.id)
                .join(Organization, OrgMembership.org_id == Organization.id)
                .where(
                    Organization.id == repository.owner_id,
                    OrgMembership.user_id == user.id,
                    OrgMembership.state == "active",
                )
            )
        ).scalar_one_or_none()
        if membership is not None:
            return True

    return False


# Requests that address a repository now look it up twice: once for this
# check and once in the endpoint's own resolver. Caching the row on the session
# keeps it at one query, which matters because a test pins the readme endpoint
# to a single repository query and because this check runs on every repository
# route. The session is per request and SQLAlchemy's identity map already
# returns one object per row, so the cache saves the round trip without
# introducing a second view of the same repository.
_CACHE_KEY = "repository_access_cache"


def cached_repository(db: AsyncSession, full_name: str) -> Repository | None:
    """Return a repository already resolved during this request."""
    return db.info.get(_CACHE_KEY, {}).get(full_name)


def remember_repository(db: AsyncSession, repository: Repository | None) -> None:
    if repository is not None:
        db.info.setdefault(_CACHE_KEY, {})[repository.full_name] = repository


async def _load(db: AsyncSession, full_name: str) -> Repository | None:
    cached = cached_repository(db, full_name)
    if cached is not None:
        return cached
    repository = (
        await db.execute(
            select(Repository)
            .options(*repository_identity_options())
            .where(Repository.full_name == full_name)
        )
    ).scalar_one_or_none()
    remember_repository(db, repository)
    return repository


async def is_hidden(db: AsyncSession, path: str, user: User | None) -> bool:
    """Whether this path names a repository the user must not be shown.

    A path that names no repository, or one that does not exist, is not this
    module's business: the endpoint's own 404 and error shape are left alone,
    and only an existing but unreadable repository is refused here.
    """
    named = repository_from_path(path)
    if named is None:
        return False
    owner, repo = named
    repository = await _load(db, f"{owner}/{repo}")
    if repository is None:
        return False
    return not await can_read(db, repository, user)
