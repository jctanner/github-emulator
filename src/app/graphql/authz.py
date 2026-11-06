"""What the presented credential may do to a repository, for GraphQL.

REST requests are scoped at the authentication chokepoint (``api.deps``): an
installation token reaches only the repositories it was minted for and only
what its permissions cover, and a workflow job token only what its job
declared, in its own repository. Both checks key off the repository in the
URL, which ``/api/graphql`` does not have, so GraphQL resolvers that act on a
repository ask here instead, with the repository they resolved.

Permissions are named as GitHub names App permissions (``pull_requests``,
``issues``). Job-token scopes spell them with a hyphen; both are accepted.
"""

from sqlalchemy import select

from app.services import job_permissions
from app.services.repository_access import can_read, can_write_contents


def _norm(scope: str) -> str:
    return str(scope).lower().replace("-", "_")


def _state(info):
    request = info.context.get("request")
    return getattr(request, "state", None)


def _installation_covers(installation_token, repo) -> bool:
    selected = [str(item).lower() for item in (installation_token.repositories or [])]
    if selected:
        return repo.full_name.lower() in selected
    owner = repo.full_name.split("/", 1)[0].lower()
    return owner == str(installation_token.installation.account_login).lower()


def _grants(permissions: dict | None, scopes: tuple[str, ...], level: str) -> bool:
    granted = {_norm(k): str(v).lower() for k, v in (permissions or {}).items()}
    for scope in scopes:
        have = granted.get(_norm(scope), "none")
        if have == "write" or (level == "read" and have == "read"):
            return True
    return False


async def _job_grants(db, job, repo, scopes: tuple[str, ...]) -> bool:
    from app.models.actions import WorkflowRun
    from app.models.repository import Repository

    run = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == job.run_id))).scalar_one_or_none()
    if run is None or run.repo_id != repo.id:
        # A job token writes only to its own repository.
        return False
    declared = job_permissions.normalise_permissions(job.permissions)
    if declared is None:
        own = (await db.execute(select(Repository).where(Repository.id == run.repo_id))).scalar_one_or_none()
        declared = job_permissions.default_permissions_for(own.default_workflow_permissions if own else None)
        if declared is None:
            return True  # the permissive default ("write")
    return _grants(declared, scopes, "write")


async def _app_bot_grants(db, user, repo, scopes: tuple[str, ...]) -> bool | None:
    """For an App's bot user, whether its installation on the repository
    grants one of ``scopes`` for writing; None when ``user`` is not a bot."""
    from app.models.apps import AppInstallation, GitHubApp

    app = (await db.execute(select(GitHubApp).where(GitHubApp.bot_user_id == user.id))).scalar_one_or_none()
    if app is None:
        return None
    owner = repo.full_name.split("/", 1)[0]
    installations = (
        await db.execute(
            select(AppInstallation).where(
                AppInstallation.app_id == app.id, AppInstallation.account_login == owner
            )
        )
    ).scalars().all()
    return any(
        (not item.repositories or repo.full_name in item.repositories)
        and _grants(item.permissions, scopes, "write")
        for item in installations
    )


async def may_read(info, db, repo, scopes: tuple[str, ...] | None = None) -> bool:
    """Whether the caller may see ``repo``, and with ``scopes``, read under them.

    An installation token must cover the repository and, when ``scopes`` are
    given, hold one of them at read or write: a token with only
    ``metadata: read`` sees the repository but not its pull request reviews.
    """
    user = info.context.get("user")
    if not await can_read(db, repo, user):
        return False
    state = _state(info)
    installation_token = getattr(state, "installation_token", None)
    if installation_token is not None:
        if not _installation_covers(installation_token, repo):
            return False
        if scopes and not _grants(installation_token.permissions, scopes, "read"):
            return False
    return True


async def may_write(info, db, repo, scopes: tuple[str, ...]) -> bool:
    """Whether the presented credential may write ``repo`` under ``scopes``.

    An integration credential is held to its own grant: an installation token
    to its repositories and permissions, a job token to its job's permissions
    in its own repository. A user credential needs write access to the
    repository (owner, site admin, or a collaborator with push), or, for an
    App's bot user, an installation that grants one of ``scopes``.
    """
    user = info.context.get("user")
    if user is None or not await may_read(info, db, repo, scopes):
        return False
    state = _state(info)
    installation_token = getattr(state, "installation_token", None)
    if installation_token is not None:
        return _grants(installation_token.permissions, scopes, "write")
    job = getattr(state, "workflow_job", None)
    if job is not None:
        return await _job_grants(db, job, repo, scopes)
    bot = await _app_bot_grants(db, user, repo, scopes)
    if bot is not None:
        return bot
    return await can_write_contents(db, repo, user)


def is_integration(info) -> bool:
    """Whether the credential is an installation or job token, which act
    only through their own grant, not as the user they map to."""
    state = _state(info)
    return getattr(state, "installation_token", None) is not None or getattr(state, "workflow_job", None) is not None
