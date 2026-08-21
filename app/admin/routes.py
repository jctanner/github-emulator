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
from sqlalchemy import func, select
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
from app.models.import_job import ImportJob
from app.models.apps import AppInstallation, AppInstallationToken, GitHubApp
from app.api.apps import _private_key
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

router = APIRouter(prefix="/admin", tags=["admin"])


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
        return RedirectResponse(url="/admin/", status_code=302)
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
    response = RedirectResponse(url="/admin/", status_code=302)
    session_token = _sign_session(user.login)
    response.set_cookie(
        key="admin_session",
        value=session_token,
        httponly=True,
        samesite="lax",
        path="/admin",
    )
    return response


@router.get("/logout")
async def logout(request: Request):
    """Clear the admin session cookie and redirect to login."""
    response = RedirectResponse(url="/admin/login", status_code=302)
    response.delete_cookie(key="admin_session", path="/admin")
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
        return RedirectResponse(url="/admin/login", status_code=302)

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
        return RedirectResponse(url="/admin/login", status_code=302)

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
        return RedirectResponse(url="/admin/login", status_code=302)

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
        return RedirectResponse(url="/admin/login", status_code=302)

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

    response = RedirectResponse(url="/admin/users", status_code=302)
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
        return RedirectResponse(url="/admin/login", status_code=302)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return RedirectResponse(url="/admin/users", status_code=302)

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
        return RedirectResponse(url="/admin/login", status_code=302)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return RedirectResponse(url="/admin/users", status_code=302)

    user.name = name or None
    user.email = email or None
    user.site_admin = site_admin == "1"

    if password:
        user.hashed_password = hash_password(password)

    await db.commit()

    return RedirectResponse(url="/admin/users", status_code=302)


@router.post("/users/{user_id}/delete", response_class=HTMLResponse)
async def delete_user(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a user."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/admin/login", status_code=302)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user:
        await db.delete(user)
        await db.commit()

    return RedirectResponse(url="/admin/users", status_code=302)


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
        return RedirectResponse(url="/admin/login", status_code=302)

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
        return RedirectResponse(url="/admin/login", status_code=302)

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
        return RedirectResponse(url="/admin/login", status_code=302)

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
        return RedirectResponse(url="/admin/login", status_code=302)

    result = await db.execute(
        select(PersonalAccessToken).where(PersonalAccessToken.id == token_id)
    )
    token = result.scalar_one_or_none()
    if token:
        await db.delete(token)
        await db.commit()

    return RedirectResponse(url="/admin/tokens", status_code=302)


# ---------------------------------------------------------------------------
# Routes: GitHub Apps and authentication overview
# ---------------------------------------------------------------------------

@router.get("/apps", response_class=HTMLResponse)
async def list_apps(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List registered emulator Apps using redacted view models."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/admin/login", status_code=302)

    result = await db.execute(select(GitHubApp).order_by(GitHubApp.id))
    apps = [_app_view(app) for app in result.scalars().all()]
    return templates.TemplateResponse(
        request=request,
        name="apps.html",
        context=_ctx(request, admin_user=admin_user, apps=apps),
    )


@router.get("/apps/create", response_class=HTMLResponse)
async def create_app_form(request: Request):
    """Render the development-only App registration form."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse(
        request=request,
        name="app_form.html",
        context=_ctx(
            request,
            admin_user=admin_user,
            permission_groups=_permission_groups(),
            form_values={},
            created_app=None,
            created_private_key=None,
        ),
    )


@router.post("/apps/create", response_class=HTMLResponse)
async def create_app_handler(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create an emulator App and display its private key once."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/admin/login", status_code=302)

    form = await request.form()
    name = str(form.get("name", "")).strip()
    slug = str(form.get("slug", "")).strip() or name.lower().replace(" ", "-")
    app_id = str(form.get("app_id", "")).strip()
    form_values = {"name": name, "slug": slug, "app_id": app_id}
    context = {
        "permission_groups": _permission_groups(),
        "form_values": form_values,
        "created_app": None,
        "created_private_key": None,
    }
    if not name or not slug:
        return templates.TemplateResponse(
            request=request,
            name="app_form.html",
            context=_ctx(
                request,
                admin_user=admin_user,
                flash_message="App name and slug are required.",
                flash_type="error",
                **context,
            ),
            status_code=422,
        )

    duplicate = await db.execute(
        select(GitHubApp).where(
            (GitHubApp.slug == slug)
            | ((GitHubApp.app_id == app_id) if app_id else False)
        )
    )
    if duplicate.scalar_one_or_none() is not None:
        return templates.TemplateResponse(
            request=request,
            name="app_form.html",
            context=_ctx(
                request,
                admin_user=admin_user,
                flash_message="An App with that slug or App ID already exists.",
                flash_type="error",
                **context,
            ),
            status_code=409,
        )

    app = GitHubApp(
        app_id=app_id or str(secrets.randbelow(900000) + 100000),
        name=name,
        slug=slug,
        private_key_pem=_private_key(),
        permissions=_permissions_from_form(form),
    )
    db.add(app)
    await db.commit()
    await db.refresh(app)
    return templates.TemplateResponse(
        request=request,
        name="app_form.html",
        context=_ctx(
            request,
            admin_user=admin_user,
            permission_groups=_permission_groups(),
            form_values={},
            created_app=_app_view(app),
            created_private_key=app.private_key_pem,
            flash_message="App created. Copy the private key now; it will not be shown again.",
            flash_type="success",
        ),
    )


async def _app_detail_context(
    request: Request,
    db: AsyncSession,
    admin_user: str,
    app_id: str,
    **extra,
) -> dict:
    detail = await _load_app_detail(db, app_id)
    repos_result = await db.execute(select(Repository).order_by(Repository.full_name))
    users_result = await db.execute(select(User).order_by(User.login))
    orgs_result = await db.execute(select(Organization).order_by(Organization.login))
    context = {
        "app": detail["app"] if detail else None,
        "installations": detail["installations"] if detail else [],
        "repositories": list(repos_result.scalars().all()),
        "users": list(users_result.scalars().all()),
        "orgs": list(orgs_result.scalars().all()),
        "permission_groups": _permission_groups(),
        "created_installation_token": None,
    }
    context.update(extra)
    return _ctx(request, admin_user=admin_user, **context)


@router.get("/apps/{app_id}", response_class=HTMLResponse)
async def app_detail(
    request: Request,
    app_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Show redacted App metadata, installations, and token metadata."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/admin/login", status_code=302)
    context = await _app_detail_context(request, db, admin_user, app_id)
    if context["app"] is None:
        return RedirectResponse(url="/admin/apps", status_code=302)
    return templates.TemplateResponse(request=request, name="app_detail.html", context=context)


@router.post("/apps/{app_id}/installations/create")
async def create_installation_handler(
    request: Request,
    app_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Create a development-only installation for a local account."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/admin/login", status_code=302)

    detail = await _load_app_detail(db, app_id)
    if detail is None:
        return RedirectResponse(url="/admin/apps", status_code=302)
    app_result = await db.execute(select(GitHubApp).where(GitHubApp.app_id == app_id))
    app = app_result.scalar_one()
    form = await request.form()
    account_login = str(form.get("account_login", "")).strip()
    account_type = str(form.get("account_type", "User")).strip() or "User"
    repositories = sorted({str(value).strip() for value in form.getlist("repositories") if str(value).strip()})
    if not repositories:
        repositories = sorted({value.strip() for value in str(form.get("repositories_text", "")).splitlines() if value.strip()})

    account_user_result = await db.execute(select(User).where(User.login == account_login))
    account_user = account_user_result.scalar_one_or_none()
    org_result = await db.execute(select(Organization).where(Organization.login == account_login))
    organization = org_result.scalar_one_or_none()
    repos_result = await db.execute(select(Repository).where(Repository.full_name.in_(repositories))) if repositories else None
    selected_repos = list(repos_result.scalars().all()) if repos_result is not None else []
    errors = []
    if account_user is None and organization is None:
        errors.append("Choose an existing local user or organization account.")
    if len(selected_repos) != len(repositories):
        errors.append("Every selected repository must exist in the emulator.")
    if not repositories:
        errors.append("Select at least one repository.")
    if errors:
        context = await _app_detail_context(
            request,
            db,
            admin_user,
            app_id,
            flash_message=" ".join(errors),
            flash_type="error",
        )
        return templates.TemplateResponse(request=request, name="app_detail.html", context=context, status_code=422)

    existing = await db.execute(
        select(AppInstallation).where(
            AppInstallation.app_id == app.id,
            AppInstallation.account_login == account_login,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return RedirectResponse(url=f"/admin/apps/{app_id}", status_code=303)

    admin_result = await db.execute(select(User).where(User.login == admin_user))
    owner = admin_result.scalar_one_or_none() or account_user
    installation = AppInstallation(
        app_id=app.id,
        user_id=owner.id,
        account_login=account_login,
        account_type="Organization" if organization is not None else account_type,
        repositories=repositories,
        permissions=_permissions_from_form(form) or app.permissions or {},
    )
    db.add(installation)
    await db.commit()
    return RedirectResponse(url=f"/admin/apps/{app_id}", status_code=303)


@router.get("/installations/{installation_id}", response_class=HTMLResponse)
async def installation_detail(
    request: Request,
    installation_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Show an installation and safe token metadata."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/admin/login", status_code=302)
    result = await db.execute(
        select(AppInstallation).where(AppInstallation.id == installation_id)
    )
    installation = result.scalar_one_or_none()
    if installation is None:
        return RedirectResponse(url="/admin/apps", status_code=302)
    return templates.TemplateResponse(
        request=request,
        name="installation_detail.html",
        context=_ctx(
            request,
            admin_user=admin_user,
            installation=_installation_view(installation),
            permission_groups=_permission_groups(),
            created_installation_token=None,
        ),
    )


@router.post("/installations/{installation_id}/tokens/create", response_class=HTMLResponse)
async def create_installation_token_handler(
    request: Request,
    installation_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Mint a test installation token and display it only in this response."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/admin/login", status_code=302)
    result = await db.execute(
        select(AppInstallation).where(AppInstallation.id == installation_id)
    )
    installation = result.scalar_one_or_none()
    if installation is None:
        return RedirectResponse(url="/admin/apps", status_code=302)

    form = await request.form()
    requested = sorted({str(value).strip() for value in form.getlist("repositories") if str(value).strip()})
    requested = requested or list(installation.repositories or [])
    if not set(requested).issubset(set(installation.repositories or [])):
        return templates.TemplateResponse(
            request=request,
            name="installation_detail.html",
            context=_ctx(
                request,
                admin_user=admin_user,
                installation=_installation_view(installation),
                permission_groups=_permission_groups(),
                created_installation_token=None,
                flash_message="A token can only include repositories from this installation.",
                flash_type="error",
            ),
            status_code=422,
        )

    raw_token = "ghs_" + secrets.token_urlsafe(30)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    token = AppInstallationToken(
        installation_id=installation.id,
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        token_prefix=raw_token[:8],
        repositories=requested,
        permissions=_permissions_from_form(form) or installation.permissions or {},
        expires_at=expires_at,
    )
    db.add(token)
    await db.commit()
    refreshed = await db.execute(
        select(AppInstallation).where(AppInstallation.id == installation_id)
    )
    installation = refreshed.scalar_one()
    return templates.TemplateResponse(
        request=request,
        name="installation_detail.html",
        context=_ctx(
            request,
            admin_user=admin_user,
            installation=_installation_view(installation),
            permission_groups=_permission_groups(),
            created_installation_token=raw_token,
            flash_message="Installation token minted. Copy it now; it will not be shown again.",
            flash_type="success",
        ),
    )


@router.get("/auth", response_class=HTMLResponse)
async def authentication_overview(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Explain the emulator's available authentication mechanisms."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/admin/login", status_code=302)

    pat_result = await db.execute(
        select(PersonalAccessToken).order_by(PersonalAccessToken.id)
    )
    app_result = await db.execute(select(GitHubApp).order_by(GitHubApp.id))
    token_result = await db.execute(
        select(AppInstallationToken).order_by(AppInstallationToken.created_at.desc())
    )
    pats = [
        {
            "id": token.id,
            "owner": token.user.login if token.user else token.user_id,
            "name": token.name,
            "prefix": token.token_prefix or "",
            "scopes": token.scopes or [],
            "created_at": _format_dt(token.created_at),
            "last_used_at": _format_dt(token.last_used_at) or "Never",
            "expiry_state": _expiry_state(token.expires_at),
        }
        for token in pat_result.scalars().all()
    ]
    installation_tokens = [
        {
            "id": token.id,
            "prefix": token.token_prefix,
            "app_id": token.installation.app.app_id,
            "app_name": token.installation.app.name,
            "installation_id": token.installation_id,
            "created_at": _format_dt(token.created_at),
            "expires_at": _format_dt(token.expires_at),
            "expiry_state": _expiry_state(token.expires_at),
            "repositories": token.repositories or [],
        }
        for token in token_result.scalars().all()
    ]
    return templates.TemplateResponse(
        request=request,
        name="auth_overview.html",
        context=_ctx(
            request,
            admin_user=admin_user,
            pats=pats,
            apps=[_app_view(app) for app in app_result.scalars().all()],
            installation_tokens=installation_tokens,
        ),
    )


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
        return RedirectResponse(url="/admin/login", status_code=302)

    result = await db.execute(select(Repository).order_by(Repository.id))
    repos = list(result.scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="repos.html",
        context=_ctx(request, admin_user=admin_user, repos=repos),
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
        return RedirectResponse(url="/admin/login", status_code=302)

    result = await db.execute(select(Repository).where(Repository.id == repo_id))
    repo = result.scalar_one_or_none()
    if not repo:
        return RedirectResponse(url="/admin/repos", status_code=302)

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
        return RedirectResponse(url="/admin/login", status_code=302)

    result = await db.execute(select(Repository).where(Repository.id == repo_id))
    repo = result.scalar_one_or_none()
    if repo:
        await delete_repository(db, repo)

    return RedirectResponse(url="/admin/repos", status_code=302)


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
        return RedirectResponse(url="/admin/login", status_code=302)

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
        return RedirectResponse(url="/admin/login", status_code=302)

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
        return RedirectResponse(url="/admin/login", status_code=302)

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

    return RedirectResponse(url="/admin/orgs", status_code=302)


@router.get("/orgs/{org_id}", response_class=HTMLResponse)
async def edit_org_page(
    request: Request,
    org_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Render the edit-organization form."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/admin/login", status_code=302)

    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if not org:
        return RedirectResponse(url="/admin/orgs", status_code=302)

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
        return RedirectResponse(url="/admin/login", status_code=302)

    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if not org:
        return RedirectResponse(url="/admin/orgs", status_code=302)

    org.name = name or None
    org.description = description or None
    org.email = email or None
    org.blog = blog or None
    org.location = location or None
    org.company = company or None
    org.billing_email = billing_email or None

    await db.commit()

    return RedirectResponse(url="/admin/orgs", status_code=302)


@router.post("/orgs/{org_id}/delete", response_class=HTMLResponse)
async def delete_org(
    request: Request,
    org_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete an organization."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/admin/login", status_code=302)

    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if org:
        await db.delete(org)
        await db.commit()

    return RedirectResponse(url="/admin/orgs", status_code=302)


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
        return RedirectResponse(url="/admin/login", status_code=302)

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
        return RedirectResponse(url="/admin/login", status_code=302)

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
        return RedirectResponse(url="/admin/login", status_code=302)

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

    return RedirectResponse(url="/admin/import/jobs", status_code=302)


@router.get("/import/jobs", response_class=HTMLResponse)
async def import_jobs(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all import jobs."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/admin/login", status_code=302)

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
        return RedirectResponse(url="/admin/login", status_code=302)

    result = await db.execute(
        select(ImportJob).where(ImportJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if not job:
        return RedirectResponse(url="/admin/import/jobs", status_code=302)

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
