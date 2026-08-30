"""Admin panel routes for the GitHub Emulator.

Provides a web-based admin interface for managing users, tokens, and
repositories. Authentication is handled via a signed session cookie
using python-jose JWS.
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jose import JWSError, jws
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.event import Event
from app.models.issue import Issue
from app.models.organization import Organization
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.token import PersonalAccessToken
from app.models.user import User
from app.services.auth_service import ensure_app_bot
from app.models.import_job import ImportJob
from app.models.apps import AppInstallation, AppInstallationToken, GitHubApp
from app.models.actions import Runner, WorkflowJob, WorkflowRun
from app.api.apps import _client_id, _private_key
from app.services.auth_service import hash_password, verify_password
from app.services.import_service import start_single_import, start_bulk_import
from app.services.repo_service import delete_repo as delete_repository
from app.services.user_service import create_token, create_user

# ---------------------------------------------------------------------------
# Templates & Router setup
# ---------------------------------------------------------------------------

_ADMIN_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES_DIR = os.path.join(_ADMIN_DIR, "templates")
_STATIC_DIR = os.path.join(_ADMIN_DIR, "static")

templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter(prefix="/ui/_admin", tags=["admin"])


# These are emulator test permissions, not a complete GitHub App manifest.
# Keeping the vocabulary in one place makes the form and the redacted detail
# pages consistent without pretending the emulator supports every GitHub
# permission.
APP_PERMISSION_GROUPS = (
    (
        "Repository",
        (
            ("contents", "Repository contents"),
            ("issues", "Issues"),
            ("pull_requests", "Pull requests"),
            ("metadata", "Repository metadata"),
        ),
    ),
    (
        "Checks and automation",
        (
            ("actions", "Actions"),
            ("checks", "Checks"),
            ("statuses", "Commit statuses"),
            ("workflows", "Workflow files"),
        ),
    ),
    (
        "Organization",
        (
            ("members", "Organization members"),
            ("administration", "Administration"),
        ),
    ),
)
PERMISSION_LEVELS = ("read", "write")


# ---------------------------------------------------------------------------
# Session helpers (signed cookie via python-jose JWS)
# ---------------------------------------------------------------------------

_ALGORITHM = "HS256"


def _sign_session(username: str) -> str:
    """Create a JWS-signed session token containing the admin username."""
    return jws.sign(
        username.encode("utf-8"),
        settings.SECRET_KEY,
        algorithm=_ALGORITHM,
    )


def _verify_session(token: str) -> Optional[str]:
    """Verify a JWS session token and return the username, or None."""
    try:
        payload = jws.verify(token, settings.SECRET_KEY, algorithms=[_ALGORITHM])
        return payload.decode("utf-8")
    except (JWSError, Exception):
        return None


def _get_admin_user(request: Request) -> Optional[str]:
    """Extract the admin username from the session cookie."""
    token = request.cookies.get("admin_session")
    if not token:
        return None
    return _verify_session(token)


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

def _require_admin(request: Request) -> Optional[str]:
    """Return the admin username or None (used to decide redirect)."""
    return _get_admin_user(request)


# ---------------------------------------------------------------------------
# Helper to build template context
# ---------------------------------------------------------------------------

def _ctx(
    request: Request,
    admin_user: Optional[str],
    flash_message: Optional[str] = None,
    flash_type: str = "info",
    **extra,
) -> dict:
    """Build the base template context dictionary."""
    context = {
        "admin_user": admin_user,
        "flash_message": flash_message,
        "flash_type": flash_type,
    }
    context.update(extra)
    return context


def _permission_groups() -> list[dict]:
    """Return template-friendly permission groups for the admin forms."""
    return [
        {
            "name": group_name,
            "permissions": [
                {"key": key, "label": label, "levels": PERMISSION_LEVELS}
                for key, label in permissions
            ],
        }
        for group_name, permissions in APP_PERMISSION_GROUPS
    ]


def _permissions_from_form(form) -> dict:
    """Parse ``permission_<name>`` controls, omitting unselected values."""
    permissions = {}
    for _group_name, group_permissions in APP_PERMISSION_GROUPS:
        for key, _label in group_permissions:
            value = str(form.get(f"permission_{key}", "")).strip().lower()
            if value in PERMISSION_LEVELS:
                permissions[key] = value
    return permissions


def _format_dt(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def _expiry_state(value: Optional[datetime]) -> str:
    if value is None:
        return "No expiry"
    when = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if when <= now:
        return "Expired"
    return f"Expires {_format_dt(when)}"


def _app_view(app: GitHubApp) -> dict:
    """Build the redacted view model used by every Apps page."""
    return {
        "id": app.app_id,
        "database_id": app.id,
        "client_id": app.client_id,
        "name": app.name,
        "slug": app.slug,
        "owner": settings.ADMIN_USERNAME,
        "permissions": app.permissions or {},
        "created_at": _format_dt(app.created_at),
        "installation_count": len(app.installations or []),
        "has_private_key": bool(app.private_key_pem),
    }


def _installation_view(installation: AppInstallation) -> dict:
    """Build an installation view without exposing token secrets or hashes."""
    tokens = [
        {
            "id": token.id,
            "prefix": token.token_prefix,
            "created_at": _format_dt(token.created_at),
            "expires_at": _format_dt(token.expires_at),
            "expiry_state": _expiry_state(token.expires_at),
            "repositories": token.repositories or [],
            "permissions": token.permissions or {},
        }
        for token in sorted(
            installation.tokens or [],
            key=lambda item: item.created_at or datetime.min,
            reverse=True,
        )
    ]
    return {
        "id": installation.id,
        "app_id": installation.app.app_id,
        "app_name": installation.app.name,
        "account_login": installation.account_login,
        "account_type": installation.account_type,
        "repository_selection": "selected",
        "repositories": installation.repositories or [],
        "permissions": installation.permissions or {},
        "created_at": _format_dt(installation.created_at),
        "token_count": len(tokens),
        "tokens": tokens,
    }


async def _load_app_detail(db: AsyncSession, app_id: str) -> Optional[dict]:
    result = await db.execute(select(GitHubApp).where(GitHubApp.app_id == app_id))
    app = result.scalar_one_or_none()
    if app is None:
        return None
    return {
        "app": _app_view(app),
        "installations": [
            _installation_view(item)
            for item in sorted(app.installations or [], key=lambda item: item.id)
        ],
    }


# ---------------------------------------------------------------------------
# Static files mount helper
# ---------------------------------------------------------------------------

def get_static_files_app():
    """Return a StaticFiles app for the admin static directory."""
    return StaticFiles(directory=_STATIC_DIR)


# ---------------------------------------------------------------------------
# Routes: Login / Logout
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render the admin login page."""
    admin_user = _get_admin_user(request)
    if admin_user:
        return RedirectResponse(url="/ui/_admin/", status_code=302)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context=_ctx(request, admin_user=None),
    )


@router.post("/login", response_class=HTMLResponse)
async def login_handler(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Handle admin login form submission."""
    # Look up the user
    result = await db.execute(select(User).where(User.login == username))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context=_ctx(
                request,
                admin_user=None,
                flash_message="Invalid username or password.",
                flash_type="error",
            ),
        )

    if not user.site_admin:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context=_ctx(
                request,
                admin_user=None,
                flash_message="User is not a site administrator.",
                flash_type="error",
            ),
        )

    # Set signed session cookie
    response = RedirectResponse(url="/ui/_admin/", status_code=302)
    session_token = _sign_session(user.login)
    response.set_cookie(
        key="admin_session",
        value=session_token,
        httponly=True,
        samesite="lax",
        path="/ui/_admin",
    )
    return response


@router.get("/logout")
async def logout(request: Request):
    """Clear the admin session cookie and redirect to login."""
    response = RedirectResponse(url="/ui/_admin/login", status_code=302)
    response.delete_cookie(key="admin_session", path="/ui/_admin")
    return response


# ---------------------------------------------------------------------------
# Routes: Dashboard
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the admin dashboard with system statistics."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    # Gather stats
    users_count = (await db.execute(select(func.count(User.id)))).scalar() or 0
    repos_count = (await db.execute(select(func.count(Repository.id)))).scalar() or 0
    issues_count = (
        await db.execute(
            select(func.count(Issue.id)).where(Issue.state == "open")
        )
    ).scalar() or 0
    prs_count = (
        await db.execute(
            select(func.count(PullRequest.id)).where(PullRequest.merged == False)  # noqa: E712
        )
    ).scalar() or 0
    tokens_count = (
        await db.execute(select(func.count(PersonalAccessToken.id)))
    ).scalar() or 0

    # Recent events
    result = await db.execute(
        select(Event).order_by(Event.created_at.desc()).limit(20)
    )
    recent_events = list(result.scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=_ctx(
            request,
            admin_user=admin_user,
            users_count=users_count,
            repos_count=repos_count,
            issues_count=issues_count,
            prs_count=prs_count,
            tokens_count=tokens_count,
            recent_events=recent_events,
        ),
    )


# ---------------------------------------------------------------------------
# Routes: Users
# ---------------------------------------------------------------------------

@router.get("/users", response_class=HTMLResponse)
async def list_users(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all users."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(select(User).order_by(User.id))
    users = list(result.scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="users.html",
        context=_ctx(request, admin_user=admin_user, users=users),
    )


@router.get("/users/create", response_class=HTMLResponse)
async def create_user_form(request: Request):
    """Render the create-user form."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="user_form.html",
        context=_ctx(request, admin_user=admin_user, edit_user=None),
    )


@router.post("/users/create", response_class=HTMLResponse)
async def create_user_handler(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    name: str = Form(""),
    email: str = Form(""),
    site_admin: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Handle create-user form submission."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    # Check for duplicate login
    existing = await db.execute(select(User).where(User.login == login))
    if existing.scalar_one_or_none():
        return templates.TemplateResponse(
            request=request,
            name="user_form.html",
            context=_ctx(
                request,
                admin_user=admin_user,
                edit_user=None,
                flash_message=f"User '{login}' already exists.",
                flash_type="error",
            ),
        )

    is_admin = site_admin == "1"
    await create_user(
        db,
        login=login,
        password=password,
        name=name or None,
        email=email or None,
        site_admin=is_admin,
    )

    response = RedirectResponse(url="/ui/_admin/users", status_code=302)
    return response


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def edit_user_page(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Render the edit-user form."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return RedirectResponse(url="/ui/_admin/users", status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="user_form.html",
        context=_ctx(request, admin_user=admin_user, edit_user=user),
    )


@router.post("/users/{user_id}", response_class=HTMLResponse)
async def update_user_handler(
    request: Request,
    user_id: int,
    login: str = Form(...),
    password: str = Form(""),
    name: str = Form(""),
    email: str = Form(""),
    site_admin: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Handle edit-user form submission."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return RedirectResponse(url="/ui/_admin/users", status_code=302)

    user.name = name or None
    user.email = email or None
    user.site_admin = site_admin == "1"

    if password:
        user.hashed_password = hash_password(password)

    await db.commit()

    return RedirectResponse(url="/ui/_admin/users", status_code=302)


@router.post("/users/{user_id}/delete", response_class=HTMLResponse)
async def delete_user(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a user."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user:
        await db.delete(user)
        await db.commit()

    return RedirectResponse(url="/ui/_admin/users", status_code=302)


# ---------------------------------------------------------------------------
# Routes: Tokens
# ---------------------------------------------------------------------------

@router.get("/tokens", response_class=HTMLResponse)
async def list_tokens(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all personal access tokens."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(
        select(PersonalAccessToken).order_by(PersonalAccessToken.id)
    )
    tokens = list(result.scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="tokens.html",
        context=_ctx(request, admin_user=admin_user, tokens=tokens),
    )


@router.get("/tokens/create", response_class=HTMLResponse)
async def create_token_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the create-token form."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(select(User).order_by(User.login))
    users = list(result.scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="token_form.html",
        context=_ctx(request, admin_user=admin_user, users=users, created_token=None),
    )


@router.post("/tokens/create", response_class=HTMLResponse)
async def create_token_handler(
    request: Request,
    user_id: int = Form(...),
    name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Handle create-token form submission."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    # Extract scopes from the form (multiple checkboxes with same name)
    form_data = await request.form()
    scopes = form_data.getlist("scopes")

    pat, raw_token = await create_token(
        db,
        user_id=user_id,
        name=name,
        scopes=scopes,
    )

    # Re-fetch users for the form (in case they want to create another)
    result = await db.execute(select(User).order_by(User.login))
    users = list(result.scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="token_form.html",
        context=_ctx(
            request,
            admin_user=admin_user,
            users=users,
            created_token=raw_token,
            flash_message="Token created successfully. Copy it now!",
            flash_type="success",
        ),
    )


@router.post("/tokens/{token_id}/revoke", response_class=HTMLResponse)
async def revoke_token(
    request: Request,
    token_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Revoke (delete) a personal access token."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(
        select(PersonalAccessToken).where(PersonalAccessToken.id == token_id)
    )
    token = result.scalar_one_or_none()
    if token:
        await db.delete(token)
        await db.commit()

    return RedirectResponse(url="/ui/_admin/tokens", status_code=302)


# ---------------------------------------------------------------------------
# Routes: Repositories
# ---------------------------------------------------------------------------

@router.get("/repos", response_class=HTMLResponse)
async def list_repos(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all repositories."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(select(Repository).order_by(Repository.id))
    repos = list(result.scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="repos.html",
        context=_ctx(request, admin_user=admin_user, repos=repos),
    )


# ---------------------------------------------------------------------------
# Routes: Actions runners
# ---------------------------------------------------------------------------

@router.get("/runners", response_class=HTMLResponse)
async def list_runners(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List runner registrations, scope, health, and current assignments."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    runners = list(
        (
            await db.execute(
                select(Runner).order_by(Runner.name, Runner.id)
            )
        ).scalars().all()
    )

    repo_ids = {runner.repo_id for runner in runners if runner.repo_id is not None}
    org_ids = {runner.org_id for runner in runners if runner.org_id is not None}
    repositories = {}
    organizations = {}
    if repo_ids:
        repositories = {
            repo.id: repo
            for repo in (
                await db.execute(select(Repository).where(Repository.id.in_(repo_ids)))
            ).scalars().all()
        }
    if org_ids:
        organizations = {
            org.id: org
            for org in (
                await db.execute(select(Organization).where(Organization.id.in_(org_ids)))
            ).scalars().all()
        }

    current_jobs = {}
    runner_ids = [runner.id for runner in runners]
    if runner_ids:
        assignments = await db.execute(
            select(WorkflowJob, WorkflowRun, Repository)
            .join(WorkflowRun, WorkflowJob.run_id == WorkflowRun.id)
            .join(Repository, WorkflowRun.repo_id == Repository.id)
            .where(
                WorkflowJob.runner_id.in_(runner_ids),
                WorkflowJob.status == "in_progress",
            )
            .order_by(WorkflowJob.started_at.desc(), WorkflowJob.id.desc())
        )
        for job, run, repository in assignments.all():
            current_jobs.setdefault(
                job.runner_id,
                {
                    "id": job.id,
                    "name": job.name,
                    "run_id": run.id,
                    "repository": repository.full_name,
                    "url": f"/ui/{repository.full_name}/actions/jobs/{job.id}",
                },
            )

    runner_views = []
    for runner in runners:
        repository = repositories.get(runner.repo_id)
        organization = organizations.get(runner.org_id)
        if repository is not None:
            scope_type = "Repository"
            scope_name = repository.full_name
            scope_url = f"/ui/{repository.full_name}"
        elif organization is not None:
            scope_type = "Organization"
            scope_name = organization.login
            scope_url = f"/ui/{organization.login}"
        elif runner.enterprise_slug:
            scope_type = "Enterprise"
            scope_name = runner.enterprise_slug
            scope_url = None
        else:
            scope_type = "Site-wide"
            scope_name = "All repositories"
            scope_url = None

        runner_views.append(
            {
                "id": runner.id,
                "name": runner.name,
                "os": runner.os,
                "status": runner.status,
                "busy": runner.busy,
                "labels": runner.labels or [],
                "scope_type": scope_type,
                "scope_name": scope_name,
                "scope_url": scope_url,
                "last_heartbeat": _format_dt(runner.last_heartbeat),
                "created_at": _format_dt(runner.created_at),
                "current_job": current_jobs.get(runner.id),
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="runners.html",
        context=_ctx(
            request,
            admin_user=admin_user,
            runners=runner_views,
            online_count=sum(runner.status == "online" for runner in runners),
            busy_count=sum(bool(runner.busy) for runner in runners),
            repository_scoped_count=sum(runner.repo_id is not None for runner in runners),
            organization_scoped_count=sum(runner.org_id is not None for runner in runners),
            enterprise_scoped_count=sum(
                runner.enterprise_slug is not None for runner in runners
            ),
            site_wide_count=sum(
                runner.repo_id is None
                and runner.org_id is None
                and runner.enterprise_slug is None
                for runner in runners
            ),
        ),
    )


@router.get("/repos/{repo_id}", response_class=HTMLResponse)
async def repo_detail(
    request: Request,
    repo_id: int,
    db: AsyncSession = Depends(get_db),
):
    """View repository details."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(select(Repository).where(Repository.id == repo_id))
    repo = result.scalar_one_or_none()
    if not repo:
        return RedirectResponse(url="/ui/_admin/repos", status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="repo_detail.html",
        context=_ctx(request, admin_user=admin_user, repo=repo),
    )


@router.post("/repos/{repo_id}/delete", response_class=HTMLResponse)
async def delete_repo(
    request: Request,
    repo_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a repository."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(select(Repository).where(Repository.id == repo_id))
    repo = result.scalar_one_or_none()
    if repo:
        await delete_repository(db, repo)

    return RedirectResponse(url="/ui/_admin/repos", status_code=302)


# ---------------------------------------------------------------------------
# Routes: Organizations
# ---------------------------------------------------------------------------

@router.get("/orgs", response_class=HTMLResponse)
async def list_orgs(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all organizations."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(select(Organization).order_by(Organization.id))
    orgs = list(result.scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="orgs.html",
        context=_ctx(request, admin_user=admin_user, orgs=orgs),
    )


@router.get("/orgs/create", response_class=HTMLResponse)
async def create_org_form(request: Request):
    """Render the create-organization form."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="org_form.html",
        context=_ctx(request, admin_user=admin_user, edit_org=None),
    )


@router.post("/orgs/create", response_class=HTMLResponse)
async def create_org_handler(
    request: Request,
    login: str = Form(...),
    name: str = Form(""),
    description: str = Form(""),
    email: str = Form(""),
    blog: str = Form(""),
    location: str = Form(""),
    company: str = Form(""),
    billing_email: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Handle create-organization form submission."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    # Check for duplicate login
    existing = await db.execute(
        select(Organization).where(Organization.login == login)
    )
    if existing.scalar_one_or_none():
        return templates.TemplateResponse(
            request=request,
            name="org_form.html",
            context=_ctx(
                request,
                admin_user=admin_user,
                edit_org=None,
                flash_message=f"Organization '{login}' already exists.",
                flash_type="error",
            ),
        )

    org = Organization(
        login=login,
        name=name or None,
        description=description or None,
        email=email or None,
        blog=blog or None,
        location=location or None,
        company=company or None,
        billing_email=billing_email or None,
    )
    db.add(org)
    await db.commit()

    return RedirectResponse(url="/ui/_admin/orgs", status_code=302)


@router.get("/orgs/{org_id}", response_class=HTMLResponse)
async def edit_org_page(
    request: Request,
    org_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Render the edit-organization form."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if not org:
        return RedirectResponse(url="/ui/_admin/orgs", status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="org_form.html",
        context=_ctx(request, admin_user=admin_user, edit_org=org),
    )


@router.post("/orgs/{org_id}", response_class=HTMLResponse)
async def update_org_handler(
    request: Request,
    org_id: int,
    login: str = Form(...),
    name: str = Form(""),
    description: str = Form(""),
    email: str = Form(""),
    blog: str = Form(""),
    location: str = Form(""),
    company: str = Form(""),
    billing_email: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Handle edit-organization form submission."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if not org:
        return RedirectResponse(url="/ui/_admin/orgs", status_code=302)

    org.name = name or None
    org.description = description or None
    org.email = email or None
    org.blog = blog or None
    org.location = location or None
    org.company = company or None
    org.billing_email = billing_email or None

    await db.commit()

    return RedirectResponse(url="/ui/_admin/orgs", status_code=302)


@router.post("/orgs/{org_id}/delete", response_class=HTMLResponse)
async def delete_org(
    request: Request,
    org_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete an organization."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if org:
        await db.delete(org)
        await db.commit()

    return RedirectResponse(url="/ui/_admin/orgs", status_code=302)


# ---------------------------------------------------------------------------
# Routes: Issues & Pull Requests (read-only browse)
# ---------------------------------------------------------------------------

@router.get("/issues", response_class=HTMLResponse)
async def list_issues(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all issues and pull requests (read-only admin view)."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(
        select(Issue).order_by(Issue.updated_at.desc())
    )
    issues = list(result.scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="issues.html",
        context=_ctx(request, admin_user=admin_user, issues=issues),
    )


# ---------------------------------------------------------------------------
# Routes: Import
# ---------------------------------------------------------------------------

@router.get("/import", response_class=HTMLResponse)
async def import_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Render the import form."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(select(User).order_by(User.login))
    users = list(result.scalars().all())
    result = await db.execute(select(Organization).order_by(Organization.login))
    orgs = list(result.scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="import_form.html",
        context=_ctx(request, admin_user=admin_user, users=users, orgs=orgs),
    )


@router.post("/import", response_class=HTMLResponse)
async def import_handler(
    request: Request,
    source_type: str = Form(...),
    source: str = Form(...),
    owner_ref: str = Form(...),
    github_token: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Handle import form submission."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    source = source.strip()
    token = github_token.strip() or None

    async def _form_users_and_orgs():
        r1 = await db.execute(select(User).order_by(User.login))
        r2 = await db.execute(select(Organization).order_by(Organization.login))
        return list(r1.scalars().all()), list(r2.scalars().all())

    if not source:
        users, orgs = await _form_users_and_orgs()
        return templates.TemplateResponse(
            request=request,
            name="import_form.html",
            context=_ctx(
                request, admin_user=admin_user, users=users, orgs=orgs,
                flash_message="Source is required.", flash_type="error",
            ),
        )

    # Parse owner_ref ("user:123" or "org:456")
    owner_type = "User"
    owner_id = None
    org_login = None

    if ":" in owner_ref:
        ref_type, ref_id = owner_ref.split(":", 1)
        if ref_type == "org":
            result = await db.execute(
                select(Organization).where(Organization.id == int(ref_id))
            )
            org = result.scalar_one_or_none()
            if not org:
                users, orgs = await _form_users_and_orgs()
                return templates.TemplateResponse(
                    request=request,
                    name="import_form.html",
                    context=_ctx(
                        request, admin_user=admin_user, users=users, orgs=orgs,
                        flash_message="Invalid organization selected.", flash_type="error",
                    ),
                )
            owner_type = "Organization"
            org_login = org.login
            # Use the admin user as owner_id (matches org repo creation pattern)
            result = await db.execute(select(User).where(User.login == admin_user))
            admin = result.scalar_one_or_none()
            if admin:
                owner_id = admin.id
            else:
                owner_id = (await db.execute(select(User.id).limit(1))).scalar()
        else:
            owner_id = int(ref_id)
    else:
        owner_id = int(owner_ref)

    # Validate owner_id
    result = await db.execute(select(User).where(User.id == owner_id))
    owner = result.scalar_one_or_none()
    if not owner:
        users, orgs = await _form_users_and_orgs()
        return templates.TemplateResponse(
            request=request,
            name="import_form.html",
            context=_ctx(
                request, admin_user=admin_user, users=users, orgs=orgs,
                flash_message="Invalid owner selected.", flash_type="error",
            ),
        )

    try:
        if source_type == "single":
            await start_single_import(db, source, owner_id, token, owner_type, org_login)
        else:
            await start_bulk_import(db, source, owner_id, token, source_type, owner_type, org_login)
    except ValueError as exc:
        users, orgs = await _form_users_and_orgs()
        return templates.TemplateResponse(
            request=request,
            name="import_form.html",
            context=_ctx(
                request, admin_user=admin_user, users=users, orgs=orgs,
                flash_message=str(exc), flash_type="error",
            ),
        )

    return RedirectResponse(url="/ui/_admin/import/jobs", status_code=302)


@router.get("/import/jobs", response_class=HTMLResponse)
async def import_jobs(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all import jobs."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(
        select(ImportJob).order_by(ImportJob.created_at.desc())
    )
    jobs = list(result.scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="import_jobs.html",
        context=_ctx(request, admin_user=admin_user, jobs=jobs),
    )


@router.get("/import/jobs/{job_id}", response_class=HTMLResponse)
async def import_job_detail(
    request: Request,
    job_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Show import job detail."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(
        select(ImportJob).where(ImportJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if not job:
        return RedirectResponse(url="/ui/_admin/import/jobs", status_code=302)

    # Load child jobs for bulk imports
    child_jobs = []
    if job.job_type == "bulk":
        result = await db.execute(
            select(ImportJob)
            .where(ImportJob.parent_job_id == job.id)
            .order_by(ImportJob.id)
        )
        child_jobs = list(result.scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="import_job_detail.html",
        context=_ctx(
            request,
            admin_user=admin_user,
            job=job,
            child_jobs=child_jobs,
        ),
    )


# Feature routers are imported after shared helpers are defined to preserve the
# existing helper import surface while breaking high-growth features out.
from app.admin.apps_routes import router as apps_router

router.include_router(apps_router)
