"""Authentication service for token and password management."""

import hashlib
import secrets
import string
from datetime import datetime
from typing import Optional

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database_retry import rollback_after_sqlite_lock
from app.models import AppInstallationToken, GitHubApp, PersonalAccessToken, User

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


async def ensure_app_bot(db: AsyncSession, app: GitHubApp) -> User:
    """Return the stable bot account associated with a GitHub App.

    GitHub installation tokens authenticate as the App installation, whose
    API actor is an App bot (for example ``fullsend-triage[bot]``), not the
    administrator who created the installation.  The emulator creates that
    account lazily so existing databases and existing Apps are upgraded on
    their next App API request.
    """
    bot = None
    if app.bot_user_id is not None:
        bot = (
            await db.execute(select(User).where(User.id == app.bot_user_id))
        ).scalar_one_or_none()
    if bot is None:
        bot_login = f"{app.slug}[bot]"
        bot = (
            await db.execute(select(User).where(User.login == bot_login))
        ).scalar_one_or_none()
        if bot is None:
            bot = User(
                login=bot_login,
                name=app.name,
                email=f"{bot_login}@users.noreply.github-emulator.local",
                hashed_password=hash_password(secrets.token_urlsafe(32)),
                site_admin=False,
                type="Bot",
            )
            db.add(bot)
            await db.flush()
        else:
            bot.name = app.name
            bot.type = "Bot"
        app.bot_user_id = bot.id
        await db.flush()
    return bot


async def get_installation_actor(
    db: AsyncSession, installation_token: AppInstallationToken
) -> User | None:
    """Resolve an installation token to its GitHub App bot actor."""
    installation = installation_token.installation
    app = installation.app
    if app is not None:
        if app.bot_user_id is None:
            await ensure_app_bot(db, app)
        if app.bot_user_id is not None:
            bot = (
                await db.execute(select(User).where(User.id == app.bot_user_id))
            ).scalar_one_or_none()
            if bot is not None:
                return bot
    return installation.user


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
        installation_token = await validate_installation_token(db, token)
        return (
            await get_installation_actor(db, installation_token)
            if installation_token
            else None
        )

    # Check expiration
    if pat.expires_at and pat.expires_at < datetime.utcnow():
        return None

    # Update last_used_at
    user_id = pat.user_id
    pat.last_used_at = datetime.utcnow()
    try:
        await db.commit()
        await db.refresh(pat)
    except Exception as exc:
        if not await rollback_after_sqlite_lock(db, exc):
            raise
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    return pat.user


async def validate_installation_token(
    db: AsyncSession, token: str
) -> Optional[AppInstallationToken]:
    """Validate a GitHub App installation token and return its token row."""
    if not token.startswith("ghs_"):
        return None

    token_hash_value = hash_token(token)
    result = await db.execute(
        select(AppInstallationToken).where(
            AppInstallationToken.token_hash == token_hash_value
        )
    )
    installation_token = result.scalar_one_or_none()
    if installation_token is None:
        return None

    expires_at = installation_token.expires_at
    if expires_at.tzinfo is None:
        if expires_at < datetime.utcnow():
            return None
    elif expires_at < datetime.now(expires_at.tzinfo):
        return None
    return installation_token


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
