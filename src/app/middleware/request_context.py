"""Name the current request for the writer-lock logging."""

from app.database_diag import current_request


class RequestContextMiddleware:
    """Record "METHOD /path" for the database logging (app/database_diag.py)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # repr-style escaping: a path's CR, LF or ESC can't start a new log line.
        path = scope["path"].encode("unicode_escape").decode("ascii")
        token = current_request.set(f"{scope['method']} {path}")
        try:
            await self.app(scope, receive, send)
        finally:
            current_request.reset(token)
