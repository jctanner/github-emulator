"""Request ID headers for GitHub-compatible request tracing."""

from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Propagate a caller request ID or generate one for every response."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = (
            request.headers.get("X-Request-Id")
            or request.headers.get("X-GitHub-Request-Id")
            or uuid4().hex
        )
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers.setdefault("X-Request-Id", request_id)
        response.headers.setdefault("X-GitHub-Request-Id", request_id)
        return response
