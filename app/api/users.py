"""User endpoints -- ``/user``, ``/users/{username}``, and admin helpers."""

import hashlib
import secrets

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession
from app.config import settings
from app.models.user import User
from app.models.token import PersonalAccessToken
from app.schemas.user import UserCreate, UserResponse, UserUpdate

router = APIRouter(tags=["users"])

BASE = settings.BASE_URL


# ---- authenticated user ---------------------------------------------------

@router.get("/user")
async def get_authenticated_user(user: AuthUser, db: DbSession):
    """Return the authenticated user's full profile."""
    return UserResponse.from_db(user, BASE)


@router.patch("/user")
async def update_authenticated_user(body: UserUpdate, user: AuthUser, db: DbSession):
    """Update the authenticated user's profile fields."""
    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(user, key, value)
    await db.commit()
    await db.refresh(user)
    return UserResponse.from_db(user, BASE)


# ---- public user profile --------------------------------------------------

@router.get("/users/{username}")
async def get_user(username: str, db: DbSession):
    """Return a public user profile."""
    result = await db.execute(select(User).where(User.login == username))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return UserResponse.from_db(user, BASE)


@router.get("/users")
async def list_users(
    db: DbSession,
    current_user: CurrentUser,
    since: int = Query(0, ge=0),
    per_page: int = Query(30, ge=1, le=100),
):
    """List all users (public, or admin-only in private installs)."""
    result = await db.execute(
        select(User)
        .where(User.id > since)
        .order_by(User.id)
        .limit(per_page)
    )
    users = result.scalars().all()
    return [UserResponse.from_db(u, BASE) for u in users]


# ---- admin helpers (not part of real GitHub API) ---------------------------

@router.post("/admin/users", status_code=201)
async def admin_create_user(body: UserCreate, db: DbSession):
    """Create a new user (admin bootstrap endpoint).

    This is **not** part of the real GitHub API.  It is provided so that
    automated test-harnesses can seed users without going through the UI.
    """
    # Check uniqueness
    existing = await db.execute(select(User).where(User.login == body.login))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=422, detail="Login already exists")

    hashed = hashlib.sha256(body.password.encode()).hexdigest()

    user = User(
        login=body.login,
        hashed_password=hashed,
        name=body.name,
        email=body.email,
        site_admin=body.site_admin,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserResponse.from_db(user, BASE)


@router.post("/admin/tokens", status_code=201)
async def admin_create_token(
    body: dict,
    db: DbSession,
):
    """Create a personal-access token for a user (admin bootstrap endpoint).

    Accepts ``{"login": "...", "name": "...", "scopes": [...]}``.
    Returns the **raw token** -- it cannot be retrieved again.
    """
    login = body.get("login")
    token_name = body.get("name", "default")
    scopes = body.get("scopes", [])

    if not login:
        raise HTTPException(status_code=422, detail="login is required")

    result = await db.execute(select(User).where(User.login == login))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    raw_token = f"ghp_{secrets.token_hex(20)}"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    pat = PersonalAccessToken(
        user_id=user.id,
        name=token_name,
        token_hash=token_hash,
        token_prefix=raw_token[:8],
        scopes=scopes,
    )
    db.add(pat)
    await db.commit()
    await db.refresh(pat)

    return {
        "id": pat.id,
        "token": raw_token,
        "name": pat.name,
        "scopes": pat.scopes,
        "created_at": pat.created_at.isoformat() + "Z" if pat.created_at else None,
    }
