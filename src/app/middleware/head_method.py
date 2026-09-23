"""HEAD support for routes that only declare GET.

HTTP defines HEAD as GET without a response body, and GitHub answers HEAD on
its GET resources accordingly. FastAPI registers only the methods a route
declares, so every ``@router.get`` endpoint here rejected HEAD with 405.

That is not a theoretical difference. A client checking a token's scopes reads
the ``X-OAuth-Scopes`` header, and the cheapest way to get a header without a
body is HEAD — which is exactly what the Fullsend CLI does against ``/user``
before it will write to a repository. Against this emulator that returned 405
and the command refused to continue, with an error about token validation that
pointed nowhere near the actual cause.

The middleware rewrites HEAD to GET for the downstream application and then
discards the body, leaving status and headers intact. Content-Length is left
as the downstream route computed it: RFC 9110 says a HEAD response SHOULD
carry the same header fields a GET would, including Content-Length, describing
the body that would have been sent.
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class HeadMethodMiddleware(BaseHTTPMiddleware):
    """Serve HEAD by running the GET route and dropping the body."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method != "HEAD":
            return await call_next(request)

        # Starlette reads the method from the ASGI scope, so rewriting it
        # there is what makes routing match the GET endpoint.
        request.scope["method"] = "GET"
        response = await call_next(request)

        body = b""
        if hasattr(response, "body_iterator"):
            async for chunk in response.body_iterator:
                body += chunk if isinstance(chunk, bytes) else chunk.encode()
        else:
            body = getattr(response, "body", b"") or b""

        headers = dict(response.headers)
        # Recompute rather than trust a streaming response's header, which may
        # be absent; a wrong Content-Length on a bodyless response is worse
        # than none at all.
        if body:
            headers["content-length"] = str(len(body))

        return Response(
            content=b"",
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )
