"""Authentication middleware for extracting and validating credentials."""

import base64
from typing import Optional

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.database import get_db
from app.models import User
from app.services.auth_service import validate_basic_auth, validate_token


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware that extracts authentication from requests.

    Supports the following Authorization header formats:
      - Authorization: token <PAT>
      - Authorization: Bearer <PAT>
      - Authorization: Basic <base64(username:password)>

    Sets request.state.user to the authenticated User or None.
    Does not block unauthenticated requests (some endpoints are public).
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request.state.user = None

        auth_header = request.headers.get("Authorization")
        if auth_header:
            from app.database import async_session

            async with async_session() as db:
                user = await _extract_user_from_auth(db, auth_header)
                request.state.user = user

        response = await call_next(request)
        return response


async def _extract_user_from_auth(
    db: AsyncSession, auth_header: str
) -> Optional[User]:
    """Extract and validate user from an Authorization header.

    Args:
        db: Async database session.
        auth_header: The raw Authorization header value.

    Returns:
        The authenticated User, or None.
    """
    parts = auth_header.split(" ", 1)
    if len(parts) != 2:
        return None

    scheme, credentials = parts[0].lower(), parts[1]

    if scheme in ("token", "bearer"):
        return await validate_token(db, credentials)

    if scheme == "basic":
        try:
            decoded = base64.b64decode(credentials).decode("utf-8")
            username, password = decoded.split(":", 1)
            return await validate_basic_auth(db, username, password)
        except Exception:
            return None

    return None


async def get_current_user(request: Request) -> Optional[User]:
    """FastAPI dependency that returns the current authenticated user.

    This can be used as a dependency in route handlers:
        @router.get("/user")
        async def get_user(user: User = Depends(get_current_user)):
            ...

    Returns:
        The authenticated User from request.state, or None.
    """
    return getattr(request.state, "user", None)


async def require_auth(request: Request) -> User:
    """FastAPI dependency that requires authentication.

    Raises a 401 error if no user is authenticated.

    Returns:
        The authenticated User.

    Raises:
        AuthenticationError: If no user is authenticated.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        from app.middleware.error_handler import AuthenticationError

        raise AuthenticationError()
    return user
