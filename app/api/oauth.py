"""OAuth endpoints -- authorize and access_token (simplified stubs)."""

import hashlib
import secrets

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select

from app.api.deps import DbSession
from app.config import settings
from app.models.user import User
from app.models.token import PersonalAccessToken

router = APIRouter(tags=["oauth"])

BASE = settings.BASE_URL


@router.get("/login/oauth/authorize")
async def authorize(
    client_id: str = Query(...),
    redirect_uri: str | None = Query(None),
    scope: str = Query(""),
    state: str = Query(""),
):
    """OAuth authorize endpoint (simplified stub).

    In a real GitHub this returns a login page. Here we return a simple
    page with a form that auto-submits a code.
    """
    code = secrets.token_hex(20)

    redirect = redirect_uri or f"{BASE}/callback"
    target = f"{redirect}?code={code}&state={state}"

    html = f"""
    <html><body>
    <h2>GitHub Emulator - Authorize</h2>
    <p>Client ID: {client_id}</p>
    <p>Scopes: {scope}</p>
    <p><a href="{target}">Click here to authorize</a></p>
    </body></html>
    """
    return HTMLResponse(content=html)


@router.post("/login/oauth/access_token")
async def access_token(body: dict, request: Request, db: DbSession):
    """OAuth access_token endpoint (simplified stub).

    Accepts ``client_id``, ``client_secret``, ``code``, ``redirect_uri``.
    Returns a token. In this emulator, we generate a real PAT for the
    admin user as a convenience.
    """
    client_id = body.get("client_id", "")
    code = body.get("code", "")

    # In a real implementation we would validate the code. For the
    # emulator we simply generate a token for the admin user.
    result = await db.execute(
        select(User).where(User.login == settings.ADMIN_USERNAME)
    )
    admin = result.scalar_one_or_none()
    if admin is None:
        raise HTTPException(status_code=500, detail="Admin user not found")

    raw_token = f"gho_{secrets.token_hex(20)}"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    pat = PersonalAccessToken(
        user_id=admin.id,
        name=f"oauth-{code[:8]}",
        token_hash=token_hash,
        token_prefix=raw_token[:8],
        scopes=["repo", "user"],
    )
    db.add(pat)
    await db.commit()

    # GitHub returns URL-encoded by default, or JSON if Accept header requests it
    accept = request.headers.get("Accept", "")
    response_data = {
        "access_token": raw_token,
        "scope": "repo,user",
        "token_type": "bearer",
    }

    if "json" in accept:
        return JSONResponse(content=response_data)
    else:
        # URL-encoded form
        encoded = "&".join(f"{k}={v}" for k, v in response_data.items())
        return HTMLResponse(content=encoded, media_type="application/x-www-form-urlencoded")
