"""Name the current request for the writer-lock logging, and release the
repository write locks it took (app/services/repository_lock.py)."""

from app.database_diag import current_request
from app.services import repository_lock


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
        locks: dict = {}
        held_token = repository_lock.held.set(locks)
        try:
            await self.app(scope, receive, send)
        finally:
            repository_lock.release_held(locks)
            repository_lock.held.reset(held_token)
            current_request.reset(token)
