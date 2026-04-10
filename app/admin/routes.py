"""Admin panel routes for the GitHub Emulator.

Provides a web-based admin interface for managing users, tokens, and
repositories. Authentication is handled via a signed session cookie
using python-jose JWS.
"""

import os
from datetime import datetime, timezone
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
from app.services.auth_service import hash_password, verify_password
from app.services.import_service import start_single_import, start_bulk_import
from app.services.user_service import create_token, create_user

# ---------------------------------------------------------------------------
# Templates & Router setup
# ---------------------------------------------------------------------------

_ADMIN_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES_DIR = os.path.join(_ADMIN_DIR, "templates")
_STATIC_DIR = os.path.join(_ADMIN_DIR, "static")

templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter(prefix="/admin", tags=["admin"])


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
        await db.delete(repo)
        await db.commit()

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

    return templates.TemplateResponse(
        request=request,
        name="import_form.html",
        context=_ctx(request, admin_user=admin_user, users=users),
    )


@router.post("/import", response_class=HTMLResponse)
async def import_handler(
    request: Request,
    source_type: str = Form(...),
    source: str = Form(...),
    owner_id: int = Form(...),
    github_token: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Handle import form submission."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/admin/login", status_code=302)

    source = source.strip()
    token = github_token.strip() or None

    if not source:
        result = await db.execute(select(User).order_by(User.login))
        users = list(result.scalars().all())
        return templates.TemplateResponse(
            request=request,
            name="import_form.html",
            context=_ctx(
                request,
                admin_user=admin_user,
                users=users,
                flash_message="Source is required.",
                flash_type="error",
            ),
        )

    # Validate owner_id
    result = await db.execute(select(User).where(User.id == owner_id))
    owner = result.scalar_one_or_none()
    if not owner:
        result = await db.execute(select(User).order_by(User.login))
        users = list(result.scalars().all())
        return templates.TemplateResponse(
            request=request,
            name="import_form.html",
            context=_ctx(
                request,
                admin_user=admin_user,
                users=users,
                flash_message="Invalid user selected.",
                flash_type="error",
            ),
        )

    try:
        if source_type == "single":
            await start_single_import(db, source, owner_id, token)
        else:
            await start_bulk_import(db, source, owner_id, token, source_type)
    except ValueError as exc:
        result = await db.execute(select(User).order_by(User.login))
        users = list(result.scalars().all())
        return templates.TemplateResponse(
            request=request,
            name="import_form.html",
            context=_ctx(
                request,
                admin_user=admin_user,
                users=users,
                flash_message=str(exc),
                flash_type="error",
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
