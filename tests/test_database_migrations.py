"""Upgrade contracts for fresh and pre-Alembic emulator databases."""

import sqlite3

from sqlalchemy import create_engine, inspect, text

from app.migrations import upgrade_database_sync


def _url(path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def _alembic_head() -> str:
    """The head revision Alembic would upgrade to, read from the scripts."""
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()



def test_fresh_database_upgrades_to_head(tmp_path, monkeypatch):
    path = tmp_path / "fresh.db"
    monkeypatch.delenv("GITHUB_EMULATOR_DATABASE_URL", raising=False)

    upgrade_database_sync(_url(path))

    engine = create_engine(f"sqlite:///{path}")
    inspector = inspect(engine)
    assert "users" in inspector.get_table_names()
    assert "workflow_jobs" in inspector.get_table_names()
    assert "issue_events" in inspector.get_table_names()
    assert "ix_issue_comments_issue_id" in {index["name"] for index in inspector.get_indexes("issue_comments")}
    assert "ix_pr_review_comments_pull_request_id" in {index["name"] for index in inspector.get_indexes("pr_review_comments")}
    with engine.connect() as connection:
        # Compare against the script directory's head rather than a literal:
        # pinning a revision means every new migration breaks this test, which
        # is how it came to be failing against 0004 while the tree was at 0005.
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == _alembic_head()
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
        assert upgraded.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == _alembic_head()
    engine.dispose()


def test_orphaned_repository_rows_are_purged_and_ids_stop_being_reused(tmp_path, monkeypatch):
    """A database from before 0011: a deleted repository's runs, jobs, secrets
    and variables are still there under its old id, and that id is what the
    next repository would get. After the upgrade they are gone and the table
    hands out fresh ids."""
    from sqlalchemy.orm import Session

    from app.models import Repository, User
    from app.models.actions import Secret, Variable, Workflow, WorkflowJob, WorkflowRun

    path = tmp_path / "reuse.db"
    monkeypatch.delenv("GITHUB_EMULATOR_DATABASE_URL", raising=False)
    upgrade_database_sync(_url(path))
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('0010_repository_actions_enabled')"))
        # Recreate the repositories table the way earlier revisions made it:
        # without AUTOINCREMENT, so the highest id can be handed out again.
        ddl = connection.execute(text("SELECT sql FROM sqlite_master WHERE name = 'repositories'")).scalar_one()
        connection.execute(text("DROP TABLE repositories"))
        connection.execute(text(ddl.replace("AUTOINCREMENT", "")))
    with Session(engine) as session:
        session.add(User(id=1, login="u", hashed_password="h"))
        session.add(Repository(id=7, owner_id=1, name="live", full_name="u/live"))
        # A fork whose parent, 9, is gone keeps its row with the pointer cleared.
        session.add(Repository(id=8, owner_id=1, name="fork", full_name="u/fork", fork=True, parent_id=9))
        session.flush()
        run_kwargs = dict(head_sha="a", head_branch="main", event="push", status="completed",
                          run_number=1, run_attempt=1, actor_id=1)
        # Owned by repository 9, which no longer exists.
        session.add(Workflow(id=1, repo_id=9, name="wf", path="p"))
        session.add(WorkflowRun(id=1, workflow_id=1, repo_id=9, **run_kwargs))
        session.add(WorkflowJob(id=1, run_id=1, name="job", status="completed"))
        session.add(Secret(repo_id=9, name="S"))
        session.add(Variable(repo_id=9, name="V", value="v"))
        # And the live repository's own run, which must survive.
        session.add(Workflow(id=2, repo_id=7, name="wf", path="p"))
        session.add(WorkflowRun(id=2, workflow_id=2, repo_id=7, **run_kwargs))
        session.commit()
    engine.dispose()

    upgrade_database_sync(_url(path))

    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        rows = lambda sql: connection.execute(text(sql)).scalar_one()  # noqa: E731
        assert rows("SELECT count(*) FROM workflow_runs WHERE repo_id = 9") == 0
        assert rows("SELECT count(*) FROM workflow_jobs") == 0
        assert rows("SELECT count(*) FROM workflows WHERE repo_id = 9") == 0
        assert rows("SELECT count(*) FROM secrets") == 0
        assert rows("SELECT count(*) FROM variables") == 0
        assert rows("SELECT count(*) FROM workflow_runs WHERE repo_id = 7") == 1
        assert rows("SELECT parent_id IS NULL FROM repositories WHERE id = 8") == 1
        assert "AUTOINCREMENT" in rows("SELECT sql FROM sqlite_master WHERE name = 'repositories'").upper()
        assert rows("SELECT seq FROM sqlite_sequence WHERE name = 'repositories'") >= 8
        assert rows("SELECT version_num FROM alembic_version") == _alembic_head()
    engine.dispose()


def test_review_thread_columns_upgrade_keeps_existing_rows(tmp_path, monkeypatch):
    """0014 adds nullable thread and minimized columns; existing rows read as
    unresolved and not minimized."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    path = tmp_path / "threads.db"
    monkeypatch.delenv("GITHUB_EMULATOR_DATABASE_URL", raising=False)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", _url(path))
    command.upgrade(config, "0013_comment_count_indexes")

    connection = sqlite3.connect(path)
    connection.executescript("""
        INSERT INTO users (id, login, hashed_password, site_admin, type) VALUES (1, 'octo', 'x', 0, 'User');
        INSERT INTO pr_review_comments (id, pull_request_id, user_id, body, path, commit_id)
            VALUES (7, 1, 1, 'existing', 'app.py', 'abc');
        INSERT INTO issue_comments (id, issue_id, user_id, body) VALUES (8, 1, 1, 'existing');
    """)
    connection.commit()
    connection.close()

    upgrade_database_sync(_url(path))

    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as db:
        row = db.execute(text(
            "SELECT body, minimized_reason, thread_resolved_at, thread_resolved_by_id"
            " FROM pr_review_comments WHERE id = 7"
        )).one()
        assert tuple(row) == ("existing", None, None, None)
        assert db.execute(text("SELECT minimized_reason FROM issue_comments WHERE id = 8")).scalar_one() is None
    columns = {column["name"] for column in inspect(engine).get_columns("reviews")}
    assert "minimized_reason" in columns
    engine.dispose()


def test_org_variable_tables_upgrade_keeps_existing_rows(tmp_path, monkeypatch):
    """0015 adds the organization variable tables beside existing data."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    path = tmp_path / "orgvars.db"
    monkeypatch.delenv("GITHUB_EMULATOR_DATABASE_URL", raising=False)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", _url(path))
    command.upgrade(config, "0014_review_threads_and_minimized_comments")

    connection = sqlite3.connect(path)
    connection.execute("INSERT INTO organizations (id, login) VALUES (3, 'acme')")
    connection.commit()
    connection.close()

    upgrade_database_sync(_url(path))

    engine = create_engine(f"sqlite:///{path}")
    tables = set(inspect(engine).get_table_names())
    assert {"org_variables", "org_variable_repositories"} <= tables
    with engine.begin() as db:
        assert db.execute(text("SELECT login FROM organizations WHERE id = 3")).scalar_one() == "acme"
        db.execute(text("INSERT INTO org_variables (org_id, name, value) VALUES (3, 'A', 'b')"))
        assert db.execute(text("SELECT visibility FROM org_variables")).scalar_one() == "all"
    engine.dispose()

    command.downgrade(config, "0014_review_threads_and_minimized_comments")
    engine = create_engine(f"sqlite:///{path}")
    assert "org_variables" not in set(inspect(engine).get_table_names())
    engine.dispose()
