from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


def _pool_args(url: str) -> dict:
    """Pool sizing for a file-backed SQLite database.

    An in-memory database uses a single shared connection (StaticPool),
    which takes no size.
    """
    if url.startswith("sqlite") and ":memory:" not in url and "mode=memory" not in url:
        return {
            "pool_size": settings.SQLITE_POOL_SIZE,
            "max_overflow": settings.SQLITE_MAX_OVERFLOW,
        }
    return {}


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={
        "check_same_thread": False,
        "timeout": settings.SQLITE_BUSY_TIMEOUT_MS / 1000,
    },
    **_pool_args(settings.DATABASE_URL),
)


@event.listens_for(engine.sync_engine, "connect")
def _configure_sqlite_connection(dbapi_connection, connection_record):
    """Apply SQLite pragmas to every pooled connection."""
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={settings.SQLITE_BUSY_TIMEOUT_MS}")
    finally:
        cursor.close()

from app.database_diag import instrument_sqlite_engine  # noqa: E402

instrument_sqlite_engine(engine)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Upgrade the schema and configure the SQLite connection policy."""
    from app.migrations import upgrade_database

    await upgrade_database(settings.DATABASE_URL)
    async with engine.begin() as conn:
        if settings.DATABASE_URL.startswith("sqlite"):
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text(f"PRAGMA busy_timeout={settings.SQLITE_BUSY_TIMEOUT_MS}"))
