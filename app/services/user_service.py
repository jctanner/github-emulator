"""User management service."""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PersonalAccessToken, User
from app.services.auth_service import generate_token, hash_password


async def create_user(
    db: AsyncSession,
    login: str,
    password: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
    site_admin: bool = False,
) -> User:
    """Create a new user.

    Args:
        db: Async database session.
        login: The user's login name (unique).
        password: Plain-text password (will be hashed).
        name: Display name.
        email: Email address.
        site_admin: Whether the user is a site administrator.

    Returns:
        The newly created User.
    """
    user = User(
        login=login,
        hashed_password=hash_password(password),
        name=name,
        email=email,
        site_admin=site_admin,
        type="User",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def get_user_by_login(db: AsyncSession, login: str) -> Optional[User]:
    """Get a user by login name.

    Returns:
        The User, or None if not found.
    """
    result = await db.execute(select(User).where(User.login == login))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    """Get a user by ID.

    Returns:
        The User, or None if not found.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def update_user(db: AsyncSession, user: User, **kwargs) -> User:
    """Update a user's attributes.

    Args:
        db: Async database session.
        user: The user to update.
        **kwargs: Fields to update (e.g. name, email, bio, etc.).

    Returns:
        The updated User.
    """
    for key, value in kwargs.items():
        if hasattr(user, key):
            setattr(user, key, value)
    await db.commit()
    await db.refresh(user)
    return user


async def list_users(
    db: AsyncSession, page: int = 1, per_page: int = 30
) -> list[User]:
    """List users with pagination.

    Args:
        db: Async database session.
        page: Page number (1-indexed).
        per_page: Number of results per page.

    Returns:
        List of Users.
    """
    offset = (page - 1) * per_page
    result = await db.execute(
        select(User).order_by(User.id).offset(offset).limit(per_page)
    )
    return list(result.scalars().all())


async def create_token(
    db: AsyncSession,
    user_id: int,
    name: str,
    scopes: Optional[list[str]] = None,
) -> tuple[PersonalAccessToken, str]:
    """Create a new personal access token for a user.

    The raw token is only available at creation time and is never
    stored in the database.

    Args:
        db: Async database session.
        user_id: The ID of the user who owns the token.
        name: A descriptive name for the token.
        scopes: List of scopes for the token.

    Returns:
        Tuple of (PersonalAccessToken object, raw_token string).
    """
    if scopes is None:
        scopes = []

    raw_token, token_hash_value, token_prefix = generate_token()

    pat = PersonalAccessToken(
        user_id=user_id,
        name=name,
        token_hash=token_hash_value,
        token_prefix=token_prefix,
        scopes=scopes,
    )
    db.add(pat)
    await db.commit()
    await db.refresh(pat)
    return pat, raw_token
