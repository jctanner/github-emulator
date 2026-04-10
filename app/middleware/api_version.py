"""API version middleware that adds GitHub-compatible version headers."""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class ApiVersionMiddleware(BaseHTTPMiddleware):
    """Middleware that adds GitHub API version headers to all responses.

    Headers added:
      - X-GitHub-Media-Type: github.v3; format=json
      - X-GitHub-Api-Version: 2022-11-28
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers["X-GitHub-Media-Type"] = "github.v3; format=json"
        response.headers["X-GitHub-Api-Version"] = "2022-11-28"
        return response
