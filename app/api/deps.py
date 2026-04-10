"""Shared FastAPI dependencies for the GitHub Emulator REST API."""

import base64
import hashlib
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.token import PersonalAccessToken
from app.models.repository import Repository
from app.config import settings


# ---------------------------------------------------------------------------
# Database session dependency
# ---------------------------------------------------------------------------

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Extract the authenticated user from the request.

    Supports:
      - ``Authorization: token <PAT>``
      - ``Authorization: Bearer <PAT>``
      - ``Authorization: Basic <base64(login:token)>``

    Returns ``None`` when no credentials are supplied.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None

    token_value: Optional[str] = None
    parts = auth_header.split(" ", 1)

    if len(parts) != 2:
        return None

    scheme, credentials = parts[0].lower(), parts[1]

    if scheme in ("token", "bearer"):
        token_value = credentials
    elif scheme == "basic":
        try:
            decoded = base64.b64decode(credentials).decode("utf-8")
            _login, _, token_value = decoded.partition(":")
        except Exception:
            return None
    else:
        return None

    if not token_value:
        return None

    # Hash the token and look it up
    token_hash = hashlib.sha256(token_value.encode()).hexdigest()
    result = await db.execute(
        select(PersonalAccessToken).where(
            PersonalAccessToken.token_hash == token_hash
        )
    )
    pat = result.scalar_one_or_none()
    if pat is None:
        return None

    # Update last_used_at
    from datetime import datetime, timezone

    pat.last_used_at = datetime.now(timezone.utc)
    await db.commit()

    return pat.user


async def require_auth(
    current_user: Optional[User] = Depends(get_current_user),
) -> User:
    """Dependency that raises 401 if the request is not authenticated."""
    if current_user is None:
        raise HTTPException(
            status_code=401,
            detail="Requires authentication",
            headers={"WWW-Authenticate": 'Basic realm="GitHub Emulator"'},
        )
    return current_user


async def get_repo_or_404(
    owner: str,
    repo: str,
    db: AsyncSession = Depends(get_db),
) -> Repository:
    """Resolve *owner/repo* to a :class:`Repository`, or raise 404."""
    full_name = f"{owner}/{repo}"
    result = await db.execute(
        select(Repository).where(Repository.full_name == full_name)
    )
    repository = result.scalar_one_or_none()
    if repository is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return repository


# ---------------------------------------------------------------------------
# Convenience type aliases
# ---------------------------------------------------------------------------

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[Optional[User], Depends(get_current_user)]
AuthUser = Annotated[User, Depends(require_auth)]
RepoDep = Annotated[Repository, Depends(get_repo_or_404)]
