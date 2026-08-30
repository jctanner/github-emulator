"""GitHub Actions OIDC issuer endpoints for local integration tests."""

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.services.oidc_service import issue, issuer, jwks

router = APIRouter(tags=["oidc"])


@router.get("/.well-known/openid-configuration")
async def configuration(request: Request):
    base = issuer()
    return {"issuer": base, "jwks_uri": f"{base}/.well-known/jwks.json", "id_token_signing_alg_values_supported": ["RS256"]}


@router.get("/.well-known/jwks.json")
async def keys():
    return jwks()


@router.get("/actions/oidc/token")
async def actions_token(request: Request, audience: str = "fullsend-mint", subject: str = "repo:fullsend-dev/triage-target:ref:refs/heads/main"):
    expected = getattr(settings, "ACTIONS_OIDC_REQUEST_TOKEN", "fullsend-action-request")
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or token != expected:
        raise HTTPException(status_code=401, detail="invalid Actions OIDC request token")
    return {"value": issue(subject, audience)}
