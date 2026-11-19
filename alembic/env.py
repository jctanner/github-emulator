"""Alembic environment configuration for async SQLAlchemy + aiosqlite."""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# -- Alembic Config object ---------------------------------------------------
config = context.config

# Interpret the config file for Python logging, on the command line only:
# the app runs migrations at startup with its logging set up already
# (``configure_logger`` is False). Keep existing loggers either way; the
# default disables them, the app's own included.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# -- Import models so Alembic sees every table --------------------------------
# Import all models to ensure they are registered on Base.metadata.
import app.models  # noqa: E402, F401
from app.database import Base  # noqa: E402

target_metadata = Base.metadata

# -- Helpers ------------------------------------------------------------------

def _get_url() -> str:
    """Return the explicitly configured URL, then environment/default values."""
    return config.attributes.get("database_url") or os.environ.get(
        "GITHUB_EMULATOR_DATABASE_URL",
        config.get_main_option("sqlalchemy.url", "sqlite+aiosqlite:///data/github_emulator.db"),
    )


# -- Offline (SQL-script) migrations -----------------------------------------

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Generates SQL scripts without connecting to the database.
    """
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# -- Online (async engine) migrations ----------------------------------------

def do_run_migrations(connection):
    """Execute migrations against a live connection (sync callback)."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using an async engine."""
    asyncio.run(run_async_migrations())


# -- Entrypoint ---------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
