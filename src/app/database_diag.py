"""Log who holds SQLite's writer lock, and for how long.

SQLite has one writer at a time. A write transaction that stays open while
its request does slow work (a git subprocess, an outbound HTTP call) makes
every other writer wait, and past the busy timeout they fail with
"database is locked" (answered 503). These events log:

- a write transaction that held the lock longer than
  ``SQLITE_SLOW_WRITE_LOG_MS``, with the request, its first write, and how
  long that first write waited for the lock. Held time runs from the first
  write's completion (the lock is then taken) to the start of the commit,
  so waiting for the lock is not counted as holding it;
- each "database is locked" error, with how long the statement waited
  (about the busy timeout: a real wait; about zero: a snapshot conflict);
- with a slow hold, where its time went: how many statements ran after the
  first write, their total database time, and the longest gap between two
  of them with the statement that ended it. A long gap is non-database work
  (or a busy event loop) inside the transaction;
- the event loop running late by more than ``EVENT_LOOP_LAG_LOG_MS``
  (``watch_event_loop_lag``), which stretches every await in a transaction.

Logger ``github_emulator.database``. Only the timing and the first SQL
words are logged, never parameters.
"""

import asyncio
import contextvars
import logging
import time

from sqlalchemy import event

from app.config import settings

logger = logging.getLogger("github_emulator.database")

# "METHOD /path" of the request using the connection, set by the middleware.
current_request: contextvars.ContextVar[str] = contextvars.ContextVar("current_request", default="-")

_WRITES = ("INSERT", "UPDATE", "DELETE", "REPLACE")


def _head(statement: str) -> str:
    return " ".join(statement.split()[:6])[:120]


def instrument_sqlite_engine(engine) -> None:
    """Attach the writer-lock timing events to a SQLite engine."""
    sync = engine.sync_engine
    if sync.dialect.name != "sqlite":
        return

    @event.listens_for(sync, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        now = time.monotonic()
        conn.info["diag_statement_start"] = now
        last_end = conn.info.get("diag_last_end")
        if "diag_write_start" in conn.info and last_end is not None:
            gap = now - last_end
            if gap > conn.info.get("diag_gap", (0.0, ""))[0]:
                conn.info["diag_gap"] = (gap, _head(statement))

    @event.listens_for(sync, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        now = time.monotonic()
        if "diag_write_start" in conn.info:
            conn.info["diag_statements"] = conn.info.get("diag_statements", 0) + 1
            conn.info["diag_db_time"] = conn.info.get("diag_db_time", 0.0) + (
                now - conn.info.get("diag_statement_start", now))
            conn.info["diag_last_end"] = now
            return
        if not statement.lstrip()[:7].upper().startswith(_WRITES):
            return
        conn.info["diag_write_start"] = now
        conn.info["diag_last_end"] = now
        conn.info["diag_write_waited"] = now - conn.info.get("diag_statement_start", now)
        conn.info["diag_write_first"] = _head(statement)
        conn.info["diag_write_request"] = current_request.get()

    def _end(conn):
        start = conn.info.pop("diag_write_start", None)
        waited = conn.info.pop("diag_write_waited", 0.0)
        first = conn.info.pop("diag_write_first", "")
        request = conn.info.pop("diag_write_request", "-")
        statements = conn.info.pop("diag_statements", 0)
        db_time = conn.info.pop("diag_db_time", 0.0)
        gap, gap_before = conn.info.pop("diag_gap", (0.0, ""))
        conn.info.pop("diag_last_end", None)
        if start is None:
            return
        held_ms = (time.monotonic() - start) * 1000
        if held_ms >= settings.SQLITE_SLOW_WRITE_LOG_MS:
            logger.warning(
                "writer lock held %.0f ms by %s (first write: %s; it waited %.0f ms for the lock;"
                " then %d statements, %.0f ms in the database, longest gap %.0f ms before: %s)",
                held_ms, request, first, waited * 1000,
                statements, db_time * 1000, gap * 1000, gap_before,
            )

    event.listen(sync, "commit", _end)
    event.listen(sync, "rollback", _end)

    @event.listens_for(sync, "handle_error")
    def _locked(context):
        if "database is locked" not in str(context.original_exception).lower():
            return
        conn = context.connection
        start = conn.info.get("diag_statement_start") if conn is not None else None
        waited = f"{(time.monotonic() - start) * 1000:.0f} ms" if start else "?"
        logger.warning(
            "database is locked after %s for %s (%s)",
            waited, current_request.get(), _head(context.statement or ""),
        )


async def watch_event_loop_lag(interval: float = 0.25) -> None:
    """Warn when the event loop runs a timer late by EVENT_LOOP_LAG_LOG_MS.

    A late loop stretches every await, including those between a
    transaction's first write and its commit. At most one line per 10 s,
    with the worst lag seen since the last line.
    """
    worst, last_log = 0.0, 0.0
    while True:
        start = time.monotonic()
        await asyncio.sleep(interval)
        lag = time.monotonic() - start - interval
        worst = max(worst, lag)
        now = time.monotonic()
        if worst * 1000 >= settings.EVENT_LOOP_LAG_LOG_MS and now - last_log >= 10:
            logger.warning("event loop ran %.0f ms late (worst in the last %.0f s)", worst * 1000,
                           now - last_log if last_log else 10)
            worst, last_log = 0.0, now


class RequestContextMiddleware:
    """Record "METHOD /path" for the database logging above."""

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
