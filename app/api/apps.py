"""Small GitHub Apps/installations surface for resettable dev stacks."""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from sqlalchemy import select

from app.api.deps import AuthUser, DbSession, CurrentUser
from app.config import settings
from app.models.apps import AppInstallation, AppInstallationToken, GitHubApp
from app.models.repository import Repository
from app.models.user import User
from app.schemas.user import _fmt_dt

router = APIRouter(tags=["apps"])


def _client_id() -> str:
    """Generate the non-secret client identifier shown by GitHub tooling."""
    return "Iv1." + secrets.token_hex(16)


def _private_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()


def _app_json(app: GitHubApp) -> dict:
    base = settings.BASE_URL.rstrip("/")
    created_at = _fmt_dt(app.created_at)
    return {
        "id": app.app_id,
        "database_id": app.id,
        "client_id": app.client_id,
        "node_id": f"A_{app.app_id}",
        "name": app.name,
        "slug": app.slug,
        "owner": {"login": settings.ADMIN_USERNAME, "type": "User"},
        "description": None,
        "external_url": f"{base}/apps/{app.slug}",
        "html_url": f"{base}/apps/{app.slug}",
        "created_at": created_at,
        "updated_at": created_at,
        "permissions": app.permissions or {},
        "events": [],
        "installations_count": len(app.installations or []),
    }


def _installation_json(app: GitHubApp, installation: AppInstallation) -> dict:
    base = settings.BASE_URL.rstrip("/")
    created_at = _fmt_dt(installation.created_at)
    repository_selection = "selected" if installation.repositories else "all"
    return {
        "id": installation.id,
        "app_id": app.app_id,
        "app_slug": app.slug,
        "target_type": installation.account_type,
        "account": {"login": installation.account_login, "type": installation.account_type},
        "repository_selection": repository_selection,
        "access_tokens_url": f"{base}/api/v3/app/installations/{installation.id}/access_tokens",
        "html_url": f"{base}/organizations/{installation.account_login}/settings/installations/{installation.id}",
        "created_at": created_at,
        "updated_at": created_at,
        "repositories": installation.repositories,
        "permissions": installation.permissions,
        "events": [],
    }


async def _app_from_jwt(request: Request, db: DbSession) -> GitHubApp:
    header = request.headers.get("Authorization", "")
    scheme, _, raw = header.partition(" ")
    if scheme.lower() != "bearer" or not raw:
        raise HTTPException(status_code=401, detail="GitHub App JWT required")
    try:
        claims = jwt.get_unverified_claims(raw)
        app_id = str(claims.get("iss", ""))
        app = (await db.execute(select(GitHubApp).where(GitHubApp.app_id == app_id))).scalar_one_or_none()
        if app is None:
            raise JWTError("unknown app")
        if settings.APP_JWT_PERMISSIVE:
            jwt.decode(
                raw,
                key="",
                options={"verify_signature": False, "verify_aud": False},
            )
        else:
            private_key = serialization.load_pem_private_key(
                app.private_key_pem.encode(), password=None
            )
            jwt.decode(
                raw,
                private_key.public_key(),
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
        return app
    except (JWTError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid GitHub App JWT") from exc


@router.post("/admin/apps", status_code=201)
async def create_app(body: dict, user: AuthUser, db: DbSession):
    if not user.site_admin:
        raise HTTPException(status_code=403, detail="site admin required")
    name = str(body.get("name", "")).strip()
    slug = str(body.get("slug", name.lower().replace(" ", "-"))).strip()
    app_id = str(body.get("app_id", "")).strip() or str(secrets.randbelow(900000) + 100000)
    if not name or not slug:
        raise HTTPException(status_code=422, detail="name is required")
    if (await db.execute(select(GitHubApp).where(GitHubApp.slug == slug))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="App slug already exists")
    app = GitHubApp(app_id=app_id, client_id=_client_id(), name=name, slug=slug, private_key_pem=_private_key(), permissions=body.get("permissions", {}))
    db.add(app)
    await db.commit()
    await db.refresh(app)
    result = _app_json(app)
    result["private_key"] = app.private_key_pem
    return result


@router.post("/admin/apps/{app_id}/installations", status_code=201)
async def create_installation(app_id: str, body: dict, user: AuthUser, db: DbSession):
    if not user.site_admin:
        raise HTTPException(status_code=403, detail="site admin required")
    app = (await db.execute(select(GitHubApp).where(GitHubApp.app_id == app_id))).scalar_one_or_none()
    if app is None:
        raise HTTPException(status_code=404, detail="App not found")
    account = str(body.get("account_login", "")).strip()
    repos = body.get("repositories", [])
    if not account or not isinstance(repos, list) or not all(isinstance(item, str) for item in repos):
        raise HTTPException(status_code=422, detail="account_login and repositories are required")
    existing = (await db.execute(select(AppInstallation).where(AppInstallation.app_id == app.id, AppInstallation.account_login == account))).scalar_one_or_none()
    if existing is not None:
        return JSONResponse(status_code=200, content=_installation_json(app, existing))
    account_user = (await db.execute(select(User).where(User.login == account))).scalar_one_or_none()
    installation = AppInstallation(
        app_id=app.id, user_id=(account_user.id if account_user else user.id), account_login=account,
        account_type=str(body.get("account_type", "Organization")),
        repositories=sorted(set(repos)), permissions=body.get("permissions", app.permissions or {}),
    )
    db.add(installation)
    await db.commit()
    await db.refresh(installation)
    return _installation_json(app, installation)


@router.get("/admin/apps/{app_id}")
async def get_admin_app(app_id: str, user: AuthUser, db: DbSession):
    if not user.site_admin:
        raise HTTPException(status_code=403, detail="site admin required")
    app = (await db.execute(select(GitHubApp).where(GitHubApp.app_id == app_id))).scalar_one_or_none()
    if app is None:
        raise HTTPException(status_code=404, detail="App not found")
    result = _app_json(app)
    # Private keys are returned only by the development-only create response.
    # Ordinary lookup/list surfaces must remain safe to render and log.
    result["has_private_key"] = bool(app.private_key_pem)
    return result


@router.get("/app")
async def get_app(request: Request, db: DbSession):
    return _app_json(await _app_from_jwt(request, db))


@router.get("/app/installations")
async def list_installations(request: Request, db: DbSession):
    app = await _app_from_jwt(request, db)
    items = (await db.execute(select(AppInstallation).where(AppInstallation.app_id == app.id))).scalars().all()
    return [_installation_json(app, item) for item in items]


@router.post("/app/installations/{installation_id}/access_tokens", status_code=201)
async def create_installation_token(installation_id: int, request: Request, body: dict, db: DbSession):
    app = await _app_from_jwt(request, db)
    installation = (await db.execute(select(AppInstallation).where(AppInstallation.id == installation_id, AppInstallation.app_id == app.id))).scalar_one_or_none()
    if installation is None:
        raise HTTPException(status_code=404, detail="Installation not found")
    requested = body.get("repositories") or installation.repositories
    if not isinstance(requested, list):
        raise HTTPException(status_code=422, detail="requested repositories must be a list")
    requested_full = [item if "/" in str(item) else f"{installation.account_login}/{item}" for item in requested]
    if not set(requested_full).issubset(set(installation.repositories)):
        raise HTTPException(status_code=422, detail="requested repository is not installed")
    permissions = body.get("permissions") or installation.permissions or {}
    raw = "ghs_" + secrets.token_urlsafe(30)
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    db.add(AppInstallationToken(installation_id=installation.id, token_hash=hashlib.sha256(raw.encode()).hexdigest(), token_prefix=raw[:8], repositories=sorted(set(requested_full)), permissions=permissions, expires_at=expires))
    await db.commit()
    return {"token": raw, "expires_at": _fmt_dt(expires), "permissions": permissions, "repository_selection": "selected" if installation.repositories else "all", "repositories": [{"full_name": item} for item in sorted(set(requested_full))]}


@router.get("/app/installations/{installation_id}/repositories")
async def list_installation_repositories(installation_id: int, request: Request, db: DbSession):
    app = await _app_from_jwt(request, db)
    installation = (await db.execute(select(AppInstallation).where(AppInstallation.id == installation_id, AppInstallation.app_id == app.id))).scalar_one_or_none()
    if installation is None:
        raise HTTPException(status_code=404, detail="Installation not found")
    repos = (await db.execute(select(Repository).where(Repository.full_name.in_(installation.repositories)))).scalars().all()
    return {"total_count": len(repos), "repositories": [{"id": repo.id, "full_name": repo.full_name, "name": repo.name, "private": repo.private} for repo in repos]}


@router.get("/repos/{owner}/{repo}/installation")
async def repo_installation(owner: str, repo: str, db: DbSession, current_user: CurrentUser):
    full_name = f"{owner}/{repo}"
    item = (await db.execute(select(AppInstallation).where(AppInstallation.repositories.contains([full_name])))).scalars().first()
    if item is None:
        raise HTTPException(status_code=404, detail="No installation found")
    return {"id": item.id, "account": {"login": item.account_login, "type": item.account_type}, "repository_selection": "selected"}
