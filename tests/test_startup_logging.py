"""The app's own log lines: what startup writes, and the level setting."""

import logging

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.mark.asyncio
async def test_a_custom_admin_password_never_reaches_the_startup_log(db_engine, monkeypatch, caplog):
    from app import main as app_main

    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(app_main, "async_session", session_factory)
    monkeypatch.setattr(app_main.settings, "ADMIN_USERNAME", "admin")
    monkeypatch.setattr(app_main.settings, "ADMIN_PASSWORD", "s3cret-admin-pw-7f2c")

    with caplog.at_level(logging.DEBUG, logger="github_emulator"):
        await app_main._ensure_admin_user()

    assert "Created admin user: admin" in caplog.text
    assert "s3cret-admin-pw-7f2c" not in caplog.text


def test_an_unknown_log_level_falls_back_to_info(caplog):
    from app import main as app_main

    with caplog.at_level(logging.WARNING, logger="github_emulator"):
        assert app_main._log_level("verbose") == logging.INFO
    assert "Unknown GITHUB_EMULATOR_LOG_LEVEL 'verbose'" in caplog.text
    assert app_main._log_level(" warning ") == logging.WARNING
    assert app_main._log_level("debug") == logging.DEBUG
