"""Shared FastAPI dependencies for the GitHub Emulator REST API."""

import re
import base64
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.db_loaders import repository_identity_options, scalar_only_options
from app.models.user import User
from app.models.repository import Repository
from app.services import job_permissions, repository_access
from app.services.auth_service import (
    get_installation_actor,
    validate_basic_auth,
    validate_installation_token,
    validate_token,
)
from app.services.job_token_service import validate_job_token
from app.services.browser_session_service import (
    COOKIE_NAME,
    CSRF_HEADER,
    csrf_token_matches,
    verify_browser_session,
)


# ---------------------------------------------------------------------------
# Database session dependency
# ---------------------------------------------------------------------------

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Extract the authenticated user, and refuse repositories they cannot see.

    Authentication itself is in :func:`_resolve_user`. The repository check is
    wrapped around every one of its return paths deliberately: the branch that
    matters most is the unauthenticated one, and a check placed after a single
    return would have missed it.
    """
    user = await _resolve_user(request, db)
    await _refuse_hidden_repository(request, db, user)
    return user


async def _refuse_hidden_repository(
    request: Request, db: AsyncSession, user: Optional[User]
) -> None:
    """404 a request that names a private repository the caller cannot see.

    Placed at the authentication chokepoint for the same reason the job-token
    permission check below is: it covers every route without touching them
    individually. 158 of the 165 repository route handlers already resolve a
    user, and the seven that do not are runner-protocol endpoints authenticated
    by a runner token rather than a user credential, which are not repository
    reads.

    404 rather than 403, because GitHub does not confirm that a private
    repository exists to someone who cannot see it, and 403 would leak the very
    fact this hides.
    """
    if await repository_access.is_hidden(db, request.url.path, user):
        raise HTTPException(status_code=404, detail="Not Found")


async def _resolve_user(
    request: Request,
    db: AsyncSession,
) -> Optional[User]:
    """Extract the authenticated user from the request.

    Supports:
      - ``Authorization: token <PAT>``
      - ``Authorization: Bearer <PAT>``
      - ``Authorization: Basic <base64(login:token)>``

    Returns ``None`` when no credentials are supplied.
    """
    auth_header = request.headers.get("Authorization")
    session_token = request.cookies.get(COOKIE_NAME)
    prefer_browser_session = bool(session_token) and (
        not auth_header
        or request.headers.get("Sec-Fetch-Site", "").lower() == "same-origin"
    )
    if prefer_browser_session:
        username = verify_browser_session(session_token)
        if not username:
            return None
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not csrf_token_matches(
            session_token,
            request.headers.get(CSRF_HEADER),
        ):
            raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")
        result = await db.execute(
            select(User)
            .options(*scalar_only_options())
            .where(User.login == username)
        )
        user = result.scalar_one_or_none()
        if user is not None:
            request.state.browser_session = True
        return user

    if not auth_header:
        return None

    parts = auth_header.split(" ", 1)

    if len(parts) != 2:
        return None

    scheme, credentials = parts[0].lower(), parts[1]

    if scheme in ("token", "bearer"):
        token_value = credentials
    elif scheme == "basic":
        try:
            decoded = base64.b64decode(credentials).decode("utf-8")
            login, _, token_value = decoded.partition(":")
            if not token_value:
                return None
            user = await validate_basic_auth(db, login, token_value)
            return user
        except Exception:
            return None
    else:
        return None

    if not token_value:
        return None

    if token_value.startswith("ghs_"):
        installation_token = await validate_installation_token(db, token_value)
        if installation_token is None:
            return None
        request.state.installation_token = installation_token
        request.state.is_installation_token = True
        # An installation token carries exactly the repositories and
        # permissions it was minted with, and GitHub answers for them at the
        # gateway: a repository outside the token's selection is Not Found,
        # and an endpoint the token's permissions do not cover is "Resource
        # not accessible by integration". Checking here covers every route,
        # the git transport included, so a token minted at the read level
        # cannot write what its installation could.
        _check_installation_token_route(
            installation_token, request.method, request.url.path, request.url.query or "",
        )
        return await get_installation_actor(db, installation_token)
    validated = await validate_job_token(db, token_value)
    if validated is not None:
        job, run = validated
        request.state.workflow_job = job
        request.state.workflow_job_permissions = job.permissions
        # A job token is scoped by the permissions its job declared. Enforcing
        # here covers every route without touching them individually, and
        # applies only to job tokens, so other credentials are unaffected.
        # A job declaring no permissions inherits the repository's default,
        # so the run's repository has to be consulted rather than assuming
        # permissive.
        repo_default = None
        if job.permissions in (None, {}):
            repo = (await db.execute(
                select(Repository).where(Repository.id == run.repo_id)
            )).scalar_one_or_none()
            repo_default = repo.default_workflow_permissions if repo else None
        refusal = job_permissions.check(
            request.method, request.url.path, job.permissions, repo_default
        )
        if refusal is not None:
            raise HTTPException(
                status_code=403,
                detail=f"Resource not accessible by integration: {refusal}",
            )
        # A job token belongs to one repository. GitHub's GITHUB_TOKEN can
        # read other public repositories and write to none of them; here a
        # job declaring actions: write could dispatch a workflow in any
        # repository (G45, proven by probe on 2026-09-25). Writes to another
        # repository are refused at the chokepoint, whatever the route.
        target = _repository_in_path(request.url.path)
        if target is not None and request.method not in ("GET", "HEAD", "OPTIONS"):
            own = (await db.execute(
                select(Repository.full_name).where(Repository.id == run.repo_id)
            )).scalar_one_or_none()
            if own is not None and target.lower() != own.lower():
                raise HTTPException(
                    status_code=403,
                    detail=f"Resource not accessible by integration: "
                           f"job token for {own} cannot write to {target}",
                )
        return run.actor
    return await validate_token(db, token_value)


_REPO_PATH = re.compile(r"^/(?:api/v3/)?repos/([^/]+)/([^/]+)(?:/|$)")
_GIT_PATH = re.compile(r"^/([^/_][^/]*)/([^/]+?)(?:\.git)?/(?:git-receive-pack|git-upload-pack|info/refs)")


def _repository_in_path(path: str) -> str | None:
    """owner/repo named by a repository API route or a git transport route."""
    m = _REPO_PATH.match(path) or _GIT_PATH.match(path)
    return f"{m.group(1)}/{m.group(2)}" if m else None


_INSTALLATION_SCOPES = (
    ("actions/variables", "actions-variables"),
    ("actions/secrets", "secrets"),
    ("actions/organization-variables", "actions-variables"),
    ("actions/organization-secrets", "secrets"),
)


def _installation_scope(path: str) -> str | None:
    """The App permission that governs a repository route the job-token map
    files under a coarser scope, or None when the map's answer stands."""
    m = _REPO_PATH.match(path)
    if not m:
        return None
    rest = path[m.end():].split("?", 1)[0]
    for prefix, scope in _INSTALLATION_SCOPES:
        if rest == prefix or rest.startswith(prefix + "/"):
            return scope
    return None


def _check_installation_token_route(token, method: str, path: str, query: str = "") -> None:
    """Refuse a route an installation token's scope does not cover."""
    target = _repository_in_path(path)
    if target is None:
        return
    installation = token.installation
    selected = [str(item).lower() for item in (token.repositories or [])]
    if selected:
        covered = target.lower() in selected
    else:
        # Every repository of the installed account.
        covered = target.split("/", 1)[0].lower() == str(installation.account_login).lower()
    if not covered:
        raise HTTPException(status_code=404, detail="Not Found")
    # Installation permissions are spelled pull_requests; the job-token
    # checker, whose route map this reuses, spells them pull-requests.
    permissions = {str(k).lower().replace("_", "-"): str(v).lower() for k, v in (token.permissions or {}).items()}
    is_read = method.upper() in ("GET", "HEAD", "OPTIONS")
    fine_grained = _installation_scope(path)
    if _GIT_PATH.match(path):
        needed = "write" if path.endswith("git-receive-pack") or "service=git-receive-pack" in query else "read"
        granted = permissions.get("contents", "none")
        if granted == "write" or (granted == "read" and needed == "read"):
            return
        refusal = f"the token's contents permission is {granted}, which does not allow this"
    elif fine_grained is not None:
        # An App permission the job-token map has no name for: GitHub
        # grants repository variables and secrets to Apps as their own
        # permissions (actions_variables, secrets), not under actions.
        granted = permissions.get(fine_grained, "none")
        if granted == "write" or (granted == "read" and is_read):
            return
        refusal = f"the token's {fine_grained.replace('-', '_')} permission is {granted}, which does not allow this"
    else:
        refusal = job_permissions.check(method, path, permissions)
    if refusal is not None:
        raise HTTPException(
            status_code=403,
            detail=f"Resource not accessible by integration: {refusal}",
        )


async def require_auth(
    current_user: Optional[User] = Depends(get_current_user),
) -> User:
    """Dependency that raises 401 if the request is not authenticated."""
    if current_user is None:
        raise HTTPException(
            status_code=401,
            detail="Requires authentication",
            headers={"WWW-Authenticate": 'Basic realm="GitHub Emulator"'},
        )
    return current_user


async def get_repo_or_404(
    owner: str,
    repo: str,
    db: AsyncSession = Depends(get_db),
) -> Repository:
    """Resolve *owner/repo* to a :class:`Repository`, or raise 404."""
    full_name = f"{owner}/{repo}"
    # The visibility check already resolved this row for this request.
    cached = repository_access.cached_repository(db, full_name)
    if cached is not None:
        return cached
    result = await db.execute(
        select(Repository)
        .options(*repository_identity_options())
        .where(Repository.full_name == full_name)
    )
    repository = result.scalar_one_or_none()
    if repository is None:
        raise HTTPException(status_code=404, detail="Not Found")
    repository_access.remember_repository(db, repository)
    return repository


async def get_repo_record_or_404(
    owner: str,
    repo: str,
    db: AsyncSession,
) -> Repository:
    """Resolve a repository without loading any ORM relationships.

    A row the visibility check already loaded is reused. It carries more
    eagerly-loaded relationships than this function would have asked for, which
    costs nothing and is the same row either way.
    """
    full_name = f"{owner}/{repo}"
    cached = repository_access.cached_repository(db, full_name)
    if cached is not None:
        return cached
    result = await db.execute(
        select(Repository)
        .options(*scalar_only_options())
        .where(Repository.full_name == full_name)
    )
    repository = result.scalar_one_or_none()
    if repository is None:
        raise HTTPException(status_code=404, detail="Not Found")
    repository_access.remember_repository(db, repository)
    return repository


# ---------------------------------------------------------------------------
# Convenience type aliases
# ---------------------------------------------------------------------------

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[Optional[User], Depends(get_current_user)]
AuthUser = Annotated[User, Depends(require_auth)]
RepoDep = Annotated[Repository, Depends(get_repo_or_404)]


async def require_contents_write(db: AsyncSession, repository: Repository, user: User | None) -> None:
    """403 unless the user may write the repository's contents (see
    ``repository_access.can_write_contents``)."""
    if not await repository_access.can_write_contents(db, repository, user):
        raise HTTPException(
            status_code=403,
            detail="Resource not accessible: write access to this repository is required",
        )
