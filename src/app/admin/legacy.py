"""Compatibility redirects for the former browser-admin namespace."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse


router = APIRouter(prefix="/admin", include_in_schema=False)


def _redirect(request: Request, path: str = "") -> RedirectResponse:
    """Redirect legacy browser URLs without intercepting admin API traffic."""
    if path == "api" or path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")

    suffix = f"/{path}" if path else ""
    destination = f"/ui/_admin{suffix}"
    if request.url.query:
        destination = f"{destination}?{request.url.query}"
    return RedirectResponse(url=destination, status_code=307)


@router.get("")
@router.get("/")
async def legacy_admin_root(request: Request):
    return _redirect(request)


@router.get("/{path:path}")
async def legacy_admin_page(request: Request, path: str):
    return _redirect(request, path)
