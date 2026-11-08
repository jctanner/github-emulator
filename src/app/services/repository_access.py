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
        # The organization is ``organization_id``; ``owner_id`` is the user
        # who created the repository, whose id says nothing about the
        # organization.
        membership = (
            await db.execute(
                select(OrgMembership.id).where(
                    OrgMembership.org_id == repository.organization_id,
                    OrgMembership.user_id == user.id,
                    OrgMembership.state == "active",
                )
            )
        ).scalar_one_or_none()
        if membership is not None:
            return True

    # An App's bot sees the private repositories its installation covers:
    # GitHub grants every installation metadata read on them. Without this, a
    # correctly scoped installation token was refused a private repository
    # its App was installed on.
    if await _installation_covers(db, repository, user):
        return True

    return False


async def _installation_covers(db: AsyncSession, repository: Repository, user: User) -> bool:
    """Whether ``user`` is an App's bot whose installation covers ``repository``."""
    from app.models.apps import AppInstallation, GitHubApp

    app = (
        await db.execute(select(GitHubApp).where(GitHubApp.bot_user_id == user.id))
    ).scalar_one_or_none()
    if app is None:
        return False
    owner_login = repository.full_name.split("/", 1)[0]
    installations = (
        await db.execute(
            select(AppInstallation).where(
                AppInstallation.app_id == app.id,
                AppInstallation.account_login == owner_login,
            )
        )
    ).scalars().all()
    return any(
        not item.repositories or repository.full_name in item.repositories
        for item in installations
    )


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


async def installation_role(
    db: AsyncSession, repository: Repository, user: User | None
) -> str | None:
    """The role a GitHub App installation grants ``user`` on ``repository``.

    A GitHub App acts as its bot user, and the bot's access to a repository
    comes from the App's installation, not from a collaborator row: an
    installation on the repository's owner that covers this repository
    (explicitly, or every repository when its selection is "all") grants the
    installation's ``contents`` permission. Returns "write", "read", or None
    for a user that is not an installed App's bot.
    """
    if user is None:
        return None
    from app.models.apps import AppInstallation, GitHubApp

    app = (
        await db.execute(select(GitHubApp).where(GitHubApp.bot_user_id == user.id))
    ).scalar_one_or_none()
    if app is None:
        return None
    owner_login = repository.full_name.split("/", 1)[0]
    installations = (
        await db.execute(
            select(AppInstallation).where(
                AppInstallation.app_id == app.id,
                AppInstallation.account_login == owner_login,
            )
        )
    ).scalars().all()
    role: str | None = None
    for installation in installations:
        covered = not installation.repositories or repository.full_name in installation.repositories
        if not covered:
            continue
        contents = str((installation.permissions or {}).get("contents", "")).lower()
        if contents == "write":
            return "write"
        if contents == "read":
            role = "read"
    return role


# Collaborator rows may carry GitHub's newer names for two levels.
_COLLABORATOR_ALIASES = {"write": "push", "read": "pull"}


async def repository_permission(
    db: AsyncSession, repository: Repository, user: User | None
) -> str | None:
    """A person's own permission on ``repository``, in the collaborator
    API's vocabulary ("pull", "triage", "push", "maintain", "admin"), or None.

    The repository's ``owner_id`` user - the owner of a user-owned repository,
    or whoever created an organization's - has admin. Otherwise it is the
    highest of the user's collaborator permission and, for an organization's
    repository (``organization_id``), what they hold as an active member: an
    organization owner has admin, and anyone else the permission of their
    organization roles (``app.services.org_roles``), which covers
    repositories created after a role was assigned. An App's bot is not a
    person; its access is ``installation_role``. A site admin's override is
    not a repository permission and is not reported here;
    ``can_write_contents`` and ``is_repository_admin`` honour it.
    """
    from app.services.org_roles import highest, org_role_permission

    if user is None:
        return None
    if user.id == repository.owner_id:
        return "admin"
    collaborator = (
        await db.execute(
            select(Collaborator.permission).where(
                Collaborator.repo_id == repository.id, Collaborator.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if collaborator is not None:
        collaborator = _COLLABORATOR_ALIASES.get(collaborator, collaborator)
    from_org = None
    if repository.owner_type == "Organization" and repository.organization_id is not None:
        from_org = await org_role_permission(db, repository.organization_id, user.id)
    return highest(collaborator, from_org)


async def is_repository_admin(
    db: AsyncSession, repository: Repository, user: User | None
) -> bool:
    """Whether ``user`` administers ``repository``: a site admin, or anyone
    whose ``repository_permission`` is admin."""
    if user is not None and user.site_admin:
        return True
    return await repository_permission(db, repository, user) == "admin"


async def can_write_contents(
    db: AsyncSession, repository: Repository, user: User | None
) -> bool:
    """Whether ``user`` may write this repository's contents, refs, and objects.

    A site admin may, and so may anyone whose ``repository_permission`` is
    push, maintain, or admin - the owner, such a collaborator, or a member holding such an
    organization role - and an installed App's bot may when its installation
    grants ``contents: write``. Everything else, including a bot whose
    installation grants only ``contents: read``, may not - which is what
    makes a role's permission level mean something.
    """
    if user is None:
        return False
    if user.site_admin:
        return True
    if await repository_permission(db, repository, user) in {"push", "maintain", "admin"}:
        return True
    return await installation_role(db, repository, user) == "write"
