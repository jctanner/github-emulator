"""The file database's connection pool is sized for concurrent runners.

A request holds a pooled connection from its first query until it ends.
SQLAlchemy's default 5 + 10 let a dozen runners' polls and log posts take
every connection, and other requests queued about a second for one.
"""

from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.database import _pool_args, engine


def test_a_file_database_gets_the_configured_pool():
    args = _pool_args("sqlite+aiosqlite:////tmp/emulator.db")
    assert args == {
        "pool_size": settings.SQLITE_POOL_SIZE,
        "max_overflow": settings.SQLITE_MAX_OVERFLOW,
    }
    assert settings.SQLITE_POOL_SIZE + settings.SQLITE_MAX_OVERFLOW > 15


def test_an_in_memory_database_takes_no_pool_size():
    assert _pool_args("sqlite+aiosqlite:///:memory:") == {}
    assert _pool_args("sqlite+aiosqlite:///file:x?mode=memory&cache=shared&uri=true") == {}
    # No database name is an in-memory database too.
    for url in ("sqlite+aiosqlite://", "sqlite+aiosqlite:///"):
        assert _pool_args(url) == {}
        create_async_engine(url, **_pool_args(url)).sync_engine.dispose()


def test_the_engine_uses_the_pool():
    pool = engine.sync_engine.pool
    if _pool_args(settings.DATABASE_URL):
        assert pool.size() == settings.SQLITE_POOL_SIZE
