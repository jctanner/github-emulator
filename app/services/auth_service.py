"""Authentication service for token and password management."""

import hashlib
import secrets
import string
from datetime import datetime
from typing import Optional

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PersonalAccessToken, User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain password against a bcrypt hash."""
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def hash_token(token: str) -> str:
    """Hash a token using SHA-256."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> tuple[str, str, str]:
    """Generate a new personal access token.

    Returns:
        tuple of (full_token, token_hash, token_prefix)
        - full_token: "ghp_" + 36 random alphanumeric characters
        - token_hash: SHA-256 hex digest of the full token
        - token_prefix: first 8 characters of the full token
    """
    alphabet = string.ascii_letters + string.digits
    random_part = "".join(secrets.choice(alphabet) for _ in range(36))
    full_token = f"ghp_{random_part}"
    token_hash_value = hash_token(full_token)
    token_prefix = full_token[:8]
    return full_token, token_hash_value, token_prefix


async def validate_token(db: AsyncSession, token: str) -> Optional[User]:
    """Validate a personal access token.

    Hashes the token, looks up the PersonalAccessToken by hash,
    updates last_used_at, and returns the associated user.

    Returns:
        The authenticated User, or None if the token is invalid.
    """
    token_hash_value = hash_token(token)
    result = await db.execute(
        select(PersonalAccessToken).where(
            PersonalAccessToken.token_hash == token_hash_value
        )
    )
    pat = result.scalar_one_or_none()
    if pat is None:
        return None

    # Check expiration
    if pat.expires_at and pat.expires_at < datetime.utcnow():
        return None

    # Update last_used_at
    pat.last_used_at = datetime.utcnow()
    await db.commit()
    await db.refresh(pat)

    return pat.user


async def validate_basic_auth(
    db: AsyncSession, username: str, password: str
) -> Optional[User]:
    """Validate basic authentication credentials.

    Tries password authentication first, then treats the password
    as a personal access token.

    Returns:
        The authenticated User, or None if credentials are invalid.
    """
    # Try password auth first
    result = await db.execute(select(User).where(User.login == username))
    user = result.scalar_one_or_none()
    if user and verify_password(password, user.hashed_password):
        return user

    # Try treating the password as a token
    token_user = await validate_token(db, password)
    if token_user is not None:
        return token_user

    return None
