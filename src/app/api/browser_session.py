"""Same-origin browser session API for the typed frontend."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import AuthUser, DbSession
from app.config import settings
from app.models.user import User
from app.schemas.user import UserResponse
from app.services.auth_service import verify_password
from app.services.browser_session_service import (
    COOKIE_NAME,
    csrf_token_for,
    sign_browser_session,
)


router = APIRouter(tags=["browser-session"])


class BrowserLogin(BaseModel):
    username: str
    password: str


class BrowserSessionResponse(BaseModel):
    user: UserResponse
    csrf_token: str


def _session_response(user: User, token: str) -> BrowserSessionResponse:
    return BrowserSessionResponse(
        user=UserResponse.from_db(user, settings.BASE_URL),
        csrf_token=csrf_token_for(token),
    )


@router.post("/session", response_model=BrowserSessionResponse)
async def create_browser_session(
    body: BrowserLogin,
    response: Response,
    db: DbSession,
):
    result = await db.execute(select(User).where(User.login == body.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = sign_browser_session(user.login)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        path="/",
        httponly=True,
        samesite="lax",
    )
    return _session_response(user, token)


@router.get("/session", response_model=BrowserSessionResponse)
async def get_browser_session(request: Request, user: AuthUser):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Browser session required")
    return _session_response(user, token)


@router.delete("/session", status_code=204)
async def delete_browser_session(response: Response, user: AuthUser):
    response.delete_cookie(key=COOKIE_NAME, path="/")
