"""Upgrade contracts for fresh and pre-Alembic emulator databases."""

import sqlite3

from sqlalchemy import create_engine, inspect, text

from app.migrations import upgrade_database_sync


def _url(path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def test_fresh_database_upgrades_to_head(tmp_path, monkeypatch):
    path = tmp_path / "fresh.db"
    monkeypatch.delenv("GITHUB_EMULATOR_DATABASE_URL", raising=False)

    upgrade_database_sync(_url(path))

    engine = create_engine(f"sqlite:///{path}")
    inspector = inspect(engine)
    assert "users" in inspector.get_table_names()
    assert "workflow_jobs" in inspector.get_table_names()
    assert "issue_events" in inspector.get_table_names()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0003_issue_events"
    engine.dispose()


def test_pre_alembic_database_is_upgraded_without_losing_rows(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    monkeypatch.delenv("GITHUB_EMULATOR_DATABASE_URL", raising=False)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE secrets (id INTEGER PRIMARY KEY, name VARCHAR);
        INSERT INTO secrets (id, name) VALUES (1, 'preserve-me');
        CREATE TABLE workflow_jobs (id INTEGER PRIMARY KEY);
        CREATE TABLE workflow_runs (id INTEGER PRIMARY KEY);
        CREATE TABLE runners (id INTEGER PRIMARY KEY);
        CREATE TABLE github_apps (id INTEGER PRIMARY KEY, name VARCHAR);
        INSERT INTO github_apps (id, name) VALUES (1, 'Legacy App');
        CREATE TABLE branch_protections (id INTEGER PRIMARY KEY);
        CREATE TABLE pull_requests (id INTEGER PRIMARY KEY);
        """
    )
    connection.commit()
    connection.close()

    upgrade_database_sync(_url(path))

    engine = create_engine(f"sqlite:///{path}")
    inspector = inspect(engine)
    expected = {
        "secrets": {"value"},
        "workflow_jobs": {"permissions"},
        "workflow_runs": {"concurrency_group"},
        "runners": {"enterprise_slug"},
        "github_apps": {"client_id", "bot_user_id"},
        "branch_protections": {
            "required_linear_history",
            "allow_force_pushes",
            "allow_deletions",
            "block_creations",
            "lock_branch",
            "allow_fork_syncing",
        },
        "pull_requests": {"last_push_by_id"},
    }
    for table, columns in expected.items():
        actual = {item["name"] for item in inspector.get_columns(table)}
        assert columns <= actual
    with engine.connect() as upgraded:
        assert upgraded.execute(text("SELECT name FROM secrets WHERE id = 1")).scalar_one() == "preserve-me"
        assert upgraded.execute(text("SELECT client_id FROM github_apps WHERE id = 1")).scalar_one().startswith("Iv1.")
        assert upgraded.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0003_issue_events"
    engine.dispose()
