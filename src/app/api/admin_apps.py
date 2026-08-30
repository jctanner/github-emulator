"""Authenticated JSON compatibility endpoints for GitHub App administration."""

from datetime import timezone
import secrets

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.apps import _client_id, _private_key
from app.api.deps import AuthUser, DbSession
from app.config import settings
from app.models.apps import AppInstallation, GitHubApp
from app.models.organization import Organization
from app.models.user import User
from app.services.auth_service import ensure_app_bot

router = APIRouter(prefix="/admin/api/apps", tags=["admin-apps"])


def _created_at(value) -> str | None:
    if value is None:
        return None
    when = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return when.isoformat().replace("+00:00", "Z")


def _owner(app: GitHubApp) -> str:
    return settings.ADMIN_USERNAME


def _admin_required(user: AuthUser) -> None:
    if not user.site_admin:
        raise HTTPException(status_code=403, detail="site admin required")


def _app_admin_json(app: GitHubApp) -> dict:
    return {
        "app_id": app.app_id,
        "name": app.name,
        "slug": app.slug,
        "owner": _owner(app),
        "client_id": app.client_id,
        "installations_count": len(app.installations or []),
        "created_at": _created_at(app.created_at),
    }


def _installation_admin_json(installation: AppInstallation) -> dict:
    repositories = installation.repositories or []
    repo = None
    if len(repositories) == 1:
        repo = repositories[0].split("/", 1)[1] if "/" in repositories[0] else repositories[0]
    return {
        "id": installation.id,
        "app_id": installation.app.app_id,
        "owner": installation.account_login,
        "repo": repo,
        "repositories": repositories,
        "created_at": _created_at(installation.created_at),
    }


@router.post("", status_code=201)
async def create_app(body: dict, user: AuthUser, db: DbSession):
    _admin_required(user)
    name = str(body.get("name", "")).strip()
    slug = str(body.get("slug", name.lower().replace(" ", "-")).strip())
    app_id = str(body.get("app_id", "")).strip() or str(secrets.randbelow(900000) + 100000)
    if not name or not slug:
        raise HTTPException(status_code=422, detail="name is required")
    duplicate = await db.execute(
        select(GitHubApp).where((GitHubApp.slug == slug) | (GitHubApp.app_id == app_id))
    )
    if duplicate.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="App slug or ID already exists")
    app = GitHubApp(
        app_id=app_id,
        client_id=_client_id(),
        name=name,
        slug=slug,
        private_key_pem=_private_key(),
        permissions=body.get("permissions", {}),
    )
    db.add(app)
    await db.flush()
    await ensure_app_bot(db, app)
    await db.commit()
    await db.refresh(app)
    result = _app_admin_json(app)
    result["private_key"] = app.private_key_pem
    return result


@router.get("")
async def list_apps(user: AuthUser, db: DbSession):
    _admin_required(user)
    apps = (await db.execute(select(GitHubApp).order_by(GitHubApp.id))).scalars().all()
    return [_app_admin_json(app) for app in apps]


@router.get("/{app_id}")
async def get_app(app_id: str, user: AuthUser, db: DbSession):
    _admin_required(user)
    app = (await db.execute(select(GitHubApp).where(GitHubApp.app_id == app_id))).scalar_one_or_none()
    if app is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await ensure_app_bot(db, app)
    await db.commit()
    result = _app_admin_json(app)
    result["installations"] = [_installation_admin_json(item) for item in app.installations or []]
    result["has_private_key"] = bool(app.private_key_pem)
    return result


@router.get("/{app_id}/private-key")
async def get_private_key(app_id: str, user: AuthUser, db: DbSession):
    _admin_required(user)
    app = (await db.execute(select(GitHubApp).where(GitHubApp.app_id == app_id))).scalar_one_or_none()
    if app is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return {"private_key": app.private_key_pem}


@router.post("/{app_id}/private-key/regenerate")
async def regenerate_private_key(app_id: str, user: AuthUser, db: DbSession):
    _admin_required(user)
    app = (await db.execute(select(GitHubApp).where(GitHubApp.app_id == app_id))).scalar_one_or_none()
    if app is None:
        raise HTTPException(status_code=404, detail="Not Found")
    app.private_key_pem = _private_key()
    await db.commit()
    return {"app_id": app.app_id, "private_key": app.private_key_pem}


@router.post("/{app_id}/installations", status_code=201)
async def create_installation(app_id: str, body: dict, user: AuthUser, db: DbSession):
    _admin_required(user)
    app = (await db.execute(select(GitHubApp).where(GitHubApp.app_id == app_id))).scalar_one_or_none()
    if app is None:
        raise HTTPException(status_code=404, detail="App not found")
    owner = str(body.get("owner", "")).strip()
    repo = str(body.get("repo", "")).strip() or None
    if not owner:
        raise HTTPException(status_code=422, detail="owner is required")
    existing = (await db.execute(select(AppInstallation).where(AppInstallation.app_id == app.id, AppInstallation.account_login == owner))).scalar_one_or_none()
    if existing is not None:
        return _installation_admin_json(existing)
    account_user = (await db.execute(select(User).where(User.login == owner))).scalar_one_or_none()
    organization = (await db.execute(select(Organization).where(Organization.login == owner))).scalar_one_or_none()
    repositories = []
    if repo:
        full_name = repo if "/" in repo else f"{owner}/{repo}"
        repositories = [full_name]
    installation = AppInstallation(
        app_id=app.id,
        user_id=(account_user.id if account_user is not None else user.id),
        account_login=owner,
        account_type="Organization" if organization is not None else "User",
        repositories=repositories,
        permissions=app.permissions or {},
    )
    db.add(installation)
    await db.commit()
    await db.refresh(installation)
    return _installation_admin_json(installation)
