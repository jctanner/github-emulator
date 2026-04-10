"""ETag middleware that adds conditional request support (If-None-Match / 304)."""

import hashlib

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class ETagMiddleware(BaseHTTPMiddleware):
    """Middleware that computes ETags for JSON GET responses and honours If-None-Match.

    Behaviour:
      - Only applies to GET requests that produce a 200 status with a JSON
        content-type (``application/json``).
      - After the downstream response is produced, an ETag is computed from
        the SHA-1 hash of the response body and attached as the ``ETag``
        header (wrapped in double quotes per RFC 7232).
      - If the incoming request carries an ``If-None-Match`` header whose
        value matches the computed ETag, a **304 Not Modified** response is
        returned with an empty body.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Only process GET requests; let everything else pass through.
        if request.method != "GET":
            return await call_next(request)

        response = await call_next(request)

        # Only process 200 OK responses.
        if response.status_code != 200:
            return response

        # Only process JSON responses.
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response

        # Read body from the response (call_next wraps in StreamingResponse)
        body_chunks = []
        async for chunk in response.body_iterator:
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            body_chunks.append(chunk)
        body = b"".join(body_chunks)

        # Compute the ETag from the response body.
        digest = hashlib.sha1(body).hexdigest()
        etag_value = f'"{digest}"'

        # Check If-None-Match from the request.
        if_none_match = request.headers.get("if-none-match")
        if if_none_match is not None:
            client_etags = [t.strip() for t in if_none_match.split(",")]
            if etag_value in client_etags or "*" in client_etags:
                return Response(
                    status_code=304,
                    headers={"ETag": etag_value},
                )

        # Return a new Response with the body and ETag header
        headers = dict(response.headers)
        headers["ETag"] = etag_value
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )
