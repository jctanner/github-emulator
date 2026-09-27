"""Register each request as in flight, so an OOM report can name it."""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.services.memory_watch import watch


class MemoryWatchMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        key = watch.enter(request.method, request.url.path)
        try:
            return await call_next(request)
        finally:
            watch.leave(key)
