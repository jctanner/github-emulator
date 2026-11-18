"""Read a REST body as JSON whatever Content-Type the client sent.

GitHub's REST API parses a request body as JSON without looking at the
Content-Type header. ``curl -d '{"body": "hi"}'`` sends
``application/x-www-form-urlencoded`` by default, and GitHub still creates
the comment. FastAPI parses a body as JSON only when the Content-Type says
JSON, so a ``body: dict`` route answered such a request with 422.

For ``/api/v3`` writes whose Content-Type is missing, form-encoded or
``text/plain``, this middleware reads the body. If the body is a JSON object
or array, it sets the Content-Type to ``application/json`` before routing. A
body that is not JSON, any other Content-Type and release asset uploads,
which store the client's Content-Type, pass through unchanged.
"""

import json
import re

_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_LOOSE_TYPES = {"", "application/x-www-form-urlencoded", "text/plain"}
_ASSET_UPLOAD = re.compile(r"^/api/v3/repos/[^/]+/[^/]+/releases/\d+/assets/?$")


class JsonBodyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if (
            scope["type"] != "http"
            or scope["method"] not in _METHODS
            or not path.startswith("/api/v3/")
            or _ASSET_UPLOAD.match(path)
        ):
            await self.app(scope, receive, send)
            return

        headers = list(scope["headers"])
        content_type = b""
        for name, value in headers:
            if name == b"content-type":
                content_type = value
        if content_type.decode("latin-1").split(";", 1)[0].strip().lower() not in _LOOSE_TYPES:
            await self.app(scope, receive, send)
            return

        chunks = []
        message = {"type": "http.request", "body": b"", "more_body": True}
        while message["type"] == "http.request" and message.get("more_body", False):
            message = await receive()
            if message["type"] == "http.request":
                chunks.append(message.get("body", b""))
        body = b"".join(chunks)

        if message["type"] == "http.request" and _is_json_document(body):
            headers = [(n, v) for n, v in headers if n != b"content-type"]
            headers.append((b"content-type", b"application/json"))
            scope = dict(scope, headers=headers)

        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                if message["type"] != "http.request":
                    return message
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def _is_json_document(body: bytes) -> bool:
    if body.lstrip()[:1] not in (b"{", b"["):
        return False
    try:
        json.loads(body)
    except ValueError:
        return False
    return True
