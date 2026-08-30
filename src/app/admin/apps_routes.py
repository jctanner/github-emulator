"""GitHub App and authentication administration routes."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.apps import _client_id, _private_key
from app.config import settings
from app.database import get_db
from app.models.apps import AppInstallation, AppInstallationToken, GitHubApp
from app.models.organization import Organization
from app.models.repository import Repository
from app.models.token import PersonalAccessToken
from app.models.user import User
from app.services.auth_service import ensure_app_bot
from app.admin.routes import (
    _app_view,
    _ctx,
    _expiry_state,
    _format_dt,
    _get_admin_user,
    _installation_view,
    _load_app_detail,
    _permission_groups,
    _permissions_from_form,
    templates,
)

router = APIRouter()

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
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

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
        return RedirectResponse(url="/ui/_admin/login", status_code=302)
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
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

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
        client_id=_client_id(),
        name=name,
        slug=slug,
        private_key_pem=_private_key(),
        permissions=_permissions_from_form(form),
    )
    db.add(app)
    await db.flush()
    await ensure_app_bot(db, app)
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
        return RedirectResponse(url="/ui/_admin/login", status_code=302)
    context = await _app_detail_context(request, db, admin_user, app_id)
    if context["app"] is None:
        return RedirectResponse(url="/ui/_admin/apps", status_code=302)
    return templates.TemplateResponse(request=request, name="app_detail.html", context=context)


@router.post("/apps/{app_id}/delete", response_class=HTMLResponse)
async def delete_app(
    request: Request,
    app_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete an emulator App and its installations/tokens."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(select(GitHubApp).where(GitHubApp.app_id == app_id))
    app = result.scalar_one_or_none()
    if app is not None:
        installations_result = await db.execute(
            select(AppInstallation).where(AppInstallation.app_id == app.id)
        )
        installations = list(installations_result.scalars().all())
        for installation in installations:
            await db.execute(
                sa_delete(AppInstallationToken).where(
                    AppInstallationToken.installation_id == installation.id
                )
            )
            await db.delete(installation)
        await db.delete(app)
        await db.commit()

    return RedirectResponse(url="/ui/_admin/apps", status_code=303)


@router.post("/apps/{app_id}/regenerate-key", response_class=HTMLResponse)
async def regenerate_app_key(
    request: Request,
    app_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Rotate an App key and display the replacement only once."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    result = await db.execute(select(GitHubApp).where(GitHubApp.app_id == app_id))
    app = result.scalar_one_or_none()
    if app is None:
        return RedirectResponse(url="/ui/_admin/apps", status_code=302)

    app.private_key_pem = _private_key()
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
            flash_message="App private key regenerated. Copy it now; it will not be shown again.",
            flash_type="success",
        ),
    )


@router.post("/apps/{app_id}/installations/create")
async def create_installation_handler(
    request: Request,
    app_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Create a development-only installation for a local account."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    detail = await _load_app_detail(db, app_id)
    if detail is None:
        return RedirectResponse(url="/ui/_admin/apps", status_code=302)
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
    existing_installation = existing.scalar_one_or_none()
    if existing_installation is not None:
        existing_repositories = ", ".join(existing_installation.repositories or []) or "no repositories"
        context = await _app_detail_context(
            request,
            db,
            admin_user,
            app_id,
            flash_message=(
                f"{app.name} already has installation #{existing_installation.id} "
                f"for {account_login} ({existing_repositories}). Remove that "
                "installation before creating a replacement for the same account."
            ),
            flash_type="error",
        )
        return templates.TemplateResponse(
            request=request,
            name="app_detail.html",
            context=context,
            status_code=409,
        )

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
    return RedirectResponse(url=f"/ui/_admin/apps/{app_id}", status_code=303)


@router.post("/apps/{app_id}/installations/{installation_id}/delete", response_class=HTMLResponse)
async def delete_installation(
    request: Request,
    app_id: str,
    installation_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Remove an installation and all tokens minted from it."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

    app_result = await db.execute(select(GitHubApp).where(GitHubApp.app_id == app_id))
    app = app_result.scalar_one_or_none()
    installation_result = await db.execute(
        select(AppInstallation).where(
            AppInstallation.id == installation_id,
            AppInstallation.app_id == (app.id if app is not None else -1),
        )
    )
    installation = installation_result.scalar_one_or_none()
    if installation is None:
        context = await _app_detail_context(
            request,
            db,
            admin_user,
            app_id,
            flash_message=(
                f"Installation #{installation_id} was not found for this App; "
                "nothing was removed."
            ),
            flash_type="error",
        )
        return templates.TemplateResponse(
            request=request,
            name="app_detail.html",
            context=context,
            status_code=404,
        )

    account_login = installation.account_login
    repositories = ", ".join(installation.repositories or []) or "no repositories"
    await db.execute(
        sa_delete(AppInstallationToken).where(
            AppInstallationToken.installation_id == installation.id
        )
    )
    await db.delete(installation)
    await db.commit()

    context = await _app_detail_context(
        request,
        db,
        admin_user,
        app_id,
        flash_message=(
            f"Removed installation #{installation_id} for {account_login} "
            f"({repositories}) and revoked its installation tokens."
        ),
        flash_type="success",
    )
    return templates.TemplateResponse(
        request=request,
        name="app_detail.html",
        context=context,
    )


@router.get("/installations/{installation_id}", response_class=HTMLResponse)
async def installation_detail(
    request: Request,
    installation_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Show an installation and safe token metadata."""
    admin_user = _get_admin_user(request)
    if not admin_user:
        return RedirectResponse(url="/ui/_admin/login", status_code=302)
    result = await db.execute(
        select(AppInstallation).where(AppInstallation.id == installation_id)
    )
    installation = result.scalar_one_or_none()
    if installation is None:
        return RedirectResponse(url="/ui/_admin/apps", status_code=302)
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
        return RedirectResponse(url="/ui/_admin/login", status_code=302)
    result = await db.execute(
        select(AppInstallation).where(AppInstallation.id == installation_id)
    )
    installation = result.scalar_one_or_none()
    if installation is None:
        return RedirectResponse(url="/ui/_admin/apps", status_code=302)

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
        return RedirectResponse(url="/ui/_admin/login", status_code=302)

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




__all__ = ["router"]
