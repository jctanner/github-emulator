"""Static-file host with history fallback for the API-client frontend."""

from pathlib import Path

from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException


# Vite names every asset by its content hash, so a cached copy can never be
# stale. index.html names the current assets, so browsers must revalidate it
# on each visit; without this they cache it heuristically and keep running
# the previous build after a deploy.
ASSET_CACHE = "public, max-age=31536000, immutable"
SHELL_CACHE = "no-cache"


class FrontendStaticFiles(StaticFiles):
    """Serve built assets and return `index.html` for client-side routes."""

    def __init__(self, directory: str | Path):
        super().__init__(directory=str(directory), html=True, check_dir=False)

    async def get_response(self, path: str, scope):
        response = await self._resolve(path, scope)
        if response.status_code in (200, 304):
            response.headers["Cache-Control"] = (
                ASSET_CACHE if self._looks_like_asset(path) else SHELL_CACHE
            )
        return response

    async def _resolve(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or self._looks_like_asset(path):
                raise
        else:
            if response.status_code != 404 or self._looks_like_asset(path):
                return response
        try:
            return await super().get_response("index.html", scope)
        except HTTPException:
            return HTMLResponse(
                "<h1>Frontend build unavailable</h1>"
                "<p>Run <code>make frontend-build</code>.</p>",
                status_code=503,
            )

    @staticmethod
    def _looks_like_asset(path: str) -> bool:
        # Repository routes legitimately end in filenames (for example
        # ``blob/main/README.md``). Only Vite's asset namespace should bypass
        # the SPA history fallback.
        return path.startswith("assets/")
