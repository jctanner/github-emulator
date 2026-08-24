"""Shared FastAPI dependencies for the GitHub Emulator REST API."""

import base64
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.repository import Repository
from app.services.auth_service import validate_basic_auth, validate_installation_token, validate_token


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

    parts = auth_header.split(" ", 1)

    if len(parts) != 2:
        return None

    scheme, credentials = parts[0].lower(), parts[1]

    if scheme in ("token", "bearer"):
        token_value = credentials
    elif scheme == "basic":
        try:
            decoded = base64.b64decode(credentials).decode("utf-8")
            login, _, token_value = decoded.partition(":")
            if not token_value:
                return None
            user = await validate_basic_auth(db, login, token_value)
            return user
        except Exception:
            return None
    else:
        return None

    if not token_value:
        return None

    if token_value.startswith("ghs_"):
        installation_token = await validate_installation_token(db, token_value)
        if installation_token is None:
            return None
        request.state.installation_token = installation_token
        request.state.is_installation_token = True
        return installation_token.installation.user
    return await validate_token(db, token_value)


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
