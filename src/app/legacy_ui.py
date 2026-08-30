"""Temporary ASGI mirror for the server-rendered UI.

The migration keeps the existing Jinja application available at
``/ui-legacy`` without copying its route handlers. Requests are replayed
through the canonical ``/ui`` routes and textual links, redirects, and cookie
paths are rewritten back to the legacy namespace.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


AsgiMessage = dict[str, Any]
AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
AsgiSend = Callable[[AsgiMessage], Awaitable[None]]


class LegacyUiMirror:
    """Expose an existing ``/ui`` ASGI surface under ``/ui-legacy``."""

    _SOURCE = b"/ui"
    _TARGET = b"/ui-legacy"
    _TEXT_CONTENT_TYPES = (
        b"text/",
        b"application/json",
        b"application/javascript",
    )

    def __init__(self, app, dependency_overrides_from=None):
        self.app = app
        self.dependency_overrides_from = dependency_overrides_from

    async def __call__(self, scope, receive: AsgiReceive, send: AsgiSend) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        mirrored_scope = dict(scope)
        root_path = scope.get("root_path", "")
        path = scope.get("path", "/")
        relative_path = path
        if root_path and relative_path.startswith(root_path):
            relative_path = relative_path[len(root_path):]
        if relative_path.startswith("/ui-legacy"):
            relative_path = relative_path[len("/ui-legacy"):]
        if not relative_path.startswith("/"):
            relative_path = f"/{relative_path}"

        mirrored_scope["root_path"] = ""
        mirrored_scope["path"] = f"/ui{relative_path}"
        mirrored_scope["raw_path"] = mirrored_scope["path"].encode("utf-8")

        if self.dependency_overrides_from is not None:
            self.app.dependency_overrides = (
                self.dependency_overrides_from.dependency_overrides
            )

        start_message: AsgiMessage | None = None
        body_parts: list[bytes] = []

        async def capture(message: AsgiMessage) -> None:
            nonlocal start_message
            if message["type"] == "http.response.start":
                start_message = dict(message)
                return
            if message["type"] == "http.response.body":
                body_parts.append(message.get("body", b""))
                if not message.get("more_body", False):
                    await self._send_rewritten(start_message, body_parts, send)

        await self.app(mirrored_scope, receive, capture)

    async def _send_rewritten(
        self,
        start_message: AsgiMessage | None,
        body_parts: list[bytes],
        send: AsgiSend,
    ) -> None:
        if start_message is None:
            raise RuntimeError("Mirrored UI response did not send response headers")

        headers = list(start_message.get("headers", []))
        content_type = next(
            (value.lower() for key, value in headers if key.lower() == b"content-type"),
            b"",
        )
        rewrite_body = any(
            content_type.startswith(prefix) for prefix in self._TEXT_CONTENT_TYPES
        )

        body = b"".join(body_parts)
        if rewrite_body:
            body = body.replace(self._SOURCE, self._TARGET)

        rewritten_headers = []
        for key, value in headers:
            lower_key = key.lower()
            if lower_key == b"content-length":
                continue
            if lower_key in {b"location", b"set-cookie"}:
                value = value.replace(self._SOURCE, self._TARGET)
            rewritten_headers.append((key, value))
        rewritten_headers.append((b"content-length", str(len(body)).encode("ascii")))

        start_message["headers"] = rewritten_headers
        await send(start_message)
        await send({"type": "http.response.body", "body": body})
