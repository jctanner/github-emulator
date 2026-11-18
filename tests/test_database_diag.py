"""The writer-lock logging names the holder and how long a lock error waited."""

import asyncio
import logging
import sqlite3

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

from app.database_diag import current_request, instrument_sqlite_engine


@pytest.mark.asyncio
async def test_logs_a_slow_writer_and_a_lock_wait(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr("app.database_diag.settings.SQLITE_SLOW_WRITE_LOG_MS", 50)
    path = tmp_path / "d.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", connect_args={"timeout": 0.2})
    instrument_sqlite_engine(engine)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE t (x INTEGER)"))
    caplog.set_level(logging.WARNING, logger="github_emulator.database")

    token = current_request.set("PUT /api/v3/repos/o/r/contents/f")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO t VALUES (1)"))
            await asyncio.sleep(0.1)  # slow work while holding the lock
    finally:
        current_request.reset(token)
    assert "writer lock held" in caplog.text
    assert "PUT /api/v3/repos/o/r/contents/f" in caplog.text
    assert "INSERT INTO t VALUES" in caplog.text
    assert "waited" in caplog.text

    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(OperationalError):
            async with engine.begin() as conn:
                await conn.execute(text("INSERT INTO t VALUES (2)"))
    finally:
        holder.rollback()
        holder.close()
    assert "database is locked after" in caplog.text
    await engine.dispose()


@pytest.mark.asyncio
async def test_waiting_for_the_lock_is_not_counted_as_holding_it(tmp_path, caplog, monkeypatch):
    import threading

    monkeypatch.setattr("app.database_diag.settings.SQLITE_SLOW_WRITE_LOG_MS", 150)
    path = tmp_path / "w.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", connect_args={"timeout": 5})
    instrument_sqlite_engine(engine)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE t (x INTEGER)"))
    caplog.set_level(logging.WARNING, logger="github_emulator.database")

    holder = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    threading.Timer(0.4, holder.rollback).start()
    async with engine.begin() as conn:  # waits about 0.4 s, then holds briefly
        await conn.execute(text("INSERT INTO t VALUES (1)"))
    holder.close()
    assert "writer lock held" not in caplog.text
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_slow_hold_says_where_its_time_went(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr("app.database_diag.settings.SQLITE_SLOW_WRITE_LOG_MS", 100)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'g.db'}")
    instrument_sqlite_engine(engine)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE t (x INTEGER)"))
    caplog.set_level(logging.WARNING, logger="github_emulator.database")
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO t VALUES (1)"))
        await asyncio.sleep(0.2)  # non-database work inside the transaction
        await conn.execute(text("SELECT count(*) FROM t"))
    assert "then 1 statements" in caplog.text
    assert "before: SELECT count(*) FROM t" in caplog.text
    gap = int(caplog.text.split("longest gap ")[1].split(" ms")[0])
    assert gap >= 190
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_blocked_event_loop_is_reported(caplog, monkeypatch):
    import time

    from app.database_diag import watch_event_loop_lag

    monkeypatch.setattr("app.database_diag.settings.EVENT_LOOP_LAG_LOG_MS", 100)
    caplog.set_level(logging.WARNING, logger="github_emulator.database")
    task = asyncio.create_task(watch_event_loop_lag(interval=0.05))
    await asyncio.sleep(0.06)
    time.sleep(0.3)  # block the loop
    await asyncio.sleep(0.1)
    task.cancel()
    assert "event loop ran" in caplog.text
