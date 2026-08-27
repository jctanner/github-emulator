from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={
        "check_same_thread": False,
        "timeout": settings.SQLITE_BUSY_TIMEOUT_MS / 1000,
    },
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

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Create all tables and set WAL mode."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if settings.DATABASE_URL.startswith("sqlite"):
            columns = await conn.execute(text("PRAGMA table_info(secrets)"))
            if "value" not in {row[1] for row in columns.fetchall()}:
                await conn.execute(text("ALTER TABLE secrets ADD COLUMN value TEXT"))
            jobs_columns = await conn.execute(text("PRAGMA table_info(workflow_jobs)"))
            if "permissions" not in {row[1] for row in jobs_columns.fetchall()}:
                await conn.execute(text("ALTER TABLE workflow_jobs ADD COLUMN permissions TEXT"))
            runs_columns = await conn.execute(text("PRAGMA table_info(workflow_runs)"))
            if "concurrency_group" not in {row[1] for row in runs_columns.fetchall()}:
                await conn.execute(text("ALTER TABLE workflow_runs ADD COLUMN concurrency_group TEXT"))
            apps_columns = await conn.execute(text("PRAGMA table_info(github_apps)"))
            app_column_names = {row[1] for row in apps_columns.fetchall()}
            if "client_id" not in app_column_names:
                await conn.execute(text("ALTER TABLE github_apps ADD COLUMN client_id TEXT"))
            if "bot_user_id" not in app_column_names:
                await conn.execute(text("ALTER TABLE github_apps ADD COLUMN bot_user_id INTEGER"))
            await conn.execute(
                text(
                    "UPDATE github_apps "
                    "SET client_id = 'Iv1.' || lower(hex(randomblob(16))) "
                    "WHERE client_id IS NULL OR client_id = ''"
                )
            )
            await conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_github_apps_client_id "
                    "ON github_apps (client_id)"
                )
            )
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text(f"PRAGMA busy_timeout={settings.SQLITE_BUSY_TIMEOUT_MS}"))
