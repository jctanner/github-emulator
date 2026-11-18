"""Helpers for bounded SQLite write-contention handling."""

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.middleware.error_handler import RetryableDatabaseError

logger = logging.getLogger("github_emulator.database")


def is_sqlite_database_locked(exc: BaseException) -> bool:
    """Return True when an exception is SQLite's transient writer lock."""
    if not isinstance(exc, OperationalError):
        return False
    return "database is locked" in str(exc).lower()


async def rollback_after_sqlite_lock(db: AsyncSession, exc: BaseException) -> bool:
    """Rollback a session if *exc* is a SQLite lock and report whether handled."""
    if not is_sqlite_database_locked(exc):
        return False
    await db.rollback()
    return True


async def commit_with_sqlite_retry(
    db: AsyncSession,
    *,
    label: str,
    before_retry: Callable[[], Any | Awaitable[Any]] | None = None,
) -> None:
    """Commit a replayable write transaction with bounded SQLite-lock retry.

    Callers that mutate ORM state must provide ``before_retry`` to replay that
    mutation after rollback. Non-replayable transactions intentionally surface
    a retryable error instead of treating a second empty commit as success.

    Each commit can itself wait the busy timeout for the lock, so retries stop
    once that much time has passed: a lock that stays held answers 503 after
    about one to two busy timeouts, not one per attempt. Lock errors that come
    back at once (a snapshot conflict) still get every attempt.
    """
    attempts = max(1, settings.SQLITE_WRITE_RETRY_ATTEMPTS)
    delay = max(0, settings.SQLITE_WRITE_RETRY_DELAY_MS) / 1000
    budget = max(0, settings.SQLITE_BUSY_TIMEOUT_MS) / 1000
    started = time.monotonic()

    for attempt in range(1, attempts + 1):
        try:
            await db.commit()
            return
        except OperationalError as exc:
            if not is_sqlite_database_locked(exc):
                raise
            await db.rollback()
            if attempt >= attempts or time.monotonic() - started >= budget:
                logger.warning(
                    "SQLite write lock persisted after %d attempts during %s",
                    attempt,
                    label,
                )
                raise RetryableDatabaseError() from exc
            logger.info(
                "SQLite write lock during %s; retrying attempt %d/%d",
                label,
                attempt + 1,
                attempts,
            )
            if delay:
                await asyncio.sleep(delay)
            if before_retry is not None:
                result = before_retry()
                if asyncio.iscoroutine(result):
                    await result
            else:
                raise RetryableDatabaseError(
                    "Database was busy and the write cannot be replayed safely"
                ) from exc
