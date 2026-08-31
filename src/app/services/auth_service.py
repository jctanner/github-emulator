"""Authentication service for token and password management."""

import asyncio
import hashlib
import secrets
import string
import time
from datetime import datetime, timedelta
from typing import Optional

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database_retry import rollback_after_sqlite_lock
from app.db_loaders import scalar_only_options
from app.models import AppInstallationToken, GitHubApp, PersonalAccessToken, User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_PAT_LAST_USED_INTERVAL = timedelta(minutes=5)
_pat_touch_locks: dict[int, asyncio.Lock] = {}
_BASIC_PASSWORD_CACHE_TTL_SECONDS = 30.0
_basic_password_cache: dict[str, float] = {}
_basic_password_locks: dict[str, asyncio.Lock] = {}


def _pat_last_used_is_stale(last_used_at: datetime | None, now: datetime) -> bool:
    return last_used_at is None or last_used_at < now - _PAT_LAST_USED_INTERVAL


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain password against a bcrypt hash."""
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def _basic_password_cache_key(username: str, password: str, hashed: str) -> str:
    """Return a non-reversible cache key tied to the current password hash."""
    material = f"{username}\0{password}\0{hashed}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


async def _verify_basic_password(
    username: str, password: str, hashed: str
) -> bool:
    """Verify Basic-auth passwords without blocking or stampeding the event loop."""
    cache_key = _basic_password_cache_key(username, password, hashed)
    now = time.monotonic()
    if _basic_password_cache.get(cache_key, 0.0) > now:
        return True

    lock = _basic_password_locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        if _basic_password_cache.get(cache_key, 0.0) > now:
            return True
        verified = await asyncio.to_thread(verify_password, password, hashed)
        if verified:
            _basic_password_cache[cache_key] = (
                time.monotonic() + _BASIC_PASSWORD_CACHE_TTL_SECONDS
            )
        return verified


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
            await db.execute(
                select(User)
                .options(*scalar_only_options())
                .where(User.id == app.bot_user_id)
            )
        ).scalar_one_or_none()
    if bot is None:
        bot_login = f"{app.slug}[bot]"
        bot = (
            await db.execute(
                select(User)
                .options(*scalar_only_options())
                .where(User.login == bot_login)
            )
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
                await db.execute(
                    select(User)
                    .options(*scalar_only_options())
                    .where(User.id == app.bot_user_id)
                )
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
        select(PersonalAccessToken)
        .options(*scalar_only_options())
        .where(PersonalAccessToken.token_hash == token_hash_value)
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

    user_id = pat.user_id
    now = datetime.utcnow()
    if _pat_last_used_is_stale(pat.last_used_at, now):
        pat_id = pat.id
        # End the token lookup's read transaction before waiting for another
        # request to finish its usage update. This avoids SQLite snapshot
        # promotion conflicts when a page sends several PAT-authenticated API
        # requests in parallel.
        await db.rollback()
        lock = _pat_touch_locks.setdefault(pat_id, asyncio.Lock())
        async with lock:
            pat = (
                await db.execute(
                    select(PersonalAccessToken)
                    .options(*scalar_only_options())
                    .where(PersonalAccessToken.id == pat_id)
                )
            ).scalar_one_or_none()
            if pat is None:
                return None
            now = datetime.utcnow()
            if _pat_last_used_is_stale(pat.last_used_at, now):
                pat.last_used_at = now
                try:
                    await db.commit()
                except Exception as exc:
                    if not await rollback_after_sqlite_lock(db, exc):
                        raise

    result = await db.execute(
        select(User)
        .options(*scalar_only_options())
        .where(User.id == user_id)
    )
    return result.scalar_one_or_none()


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
    # GitHub API clients commonly send PATs as the Basic password. Avoid an
    # expensive bcrypt attempt when the credential is clearly a token.
    if password.startswith(("ghp_", "ghs_")):
        token_user = await validate_token(db, password)
        if token_user is not None:
            return token_user

    # Try password auth. Bcrypt is deliberately expensive and synchronous, so
    # run it off the event loop and coalesce parallel browser requests.
    result = await db.execute(
        select(User)
        .options(*scalar_only_options())
        .where(User.login == username)
    )
    user = result.scalar_one_or_none()
    if user and await _verify_basic_password(
        username, password, user.hashed_password
    ):
        return user

    # Try treating the password as a token
    token_user = await validate_token(db, password)
    if token_user is not None:
        return token_user

    return None
