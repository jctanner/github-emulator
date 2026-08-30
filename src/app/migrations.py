"""Programmatic Alembic entry point used during application startup."""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def upgrade_database_sync(database_url: str) -> None:
    """Upgrade one configured database to the latest schema revision."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("prepend_sys_path", str(PROJECT_ROOT / "src"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    config.attributes["database_url"] = database_url
    # Alembic's async environment uses ``asyncio.run()``, which clears a
    # caller-managed current loop. Preserve it for synchronous test harnesses
    # and other embedders; normal application startup invokes this in a thread.
    try:
        previous_loop = asyncio.get_event_loop()
    except RuntimeError:
        previous_loop = None
    try:
        command.upgrade(config, "head")
    finally:
        if previous_loop is not None:
            asyncio.set_event_loop(previous_loop)


async def upgrade_database(database_url: str) -> None:
    """Run synchronous Alembic machinery without blocking the event loop."""
    await asyncio.to_thread(upgrade_database_sync, database_url)
