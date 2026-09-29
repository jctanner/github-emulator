"""Deleting a repository takes everything it owns, and its id is never reused."""

import os
from datetime import datetime

import pytest
from sqlalchemy import func, select, text

from app.config import settings
from app.database import Base
from app.models import Repository, User
from app.models.actions import Secret, Variable, Workflow, WorkflowJob, WorkflowRun
from app.models.artifact import WorkflowArtifact
from app.services.repository_purge import orphan_purge_statements, purge_statements
from tests.conftest import auth_headers

API = "/api/v3"


async def _create(client, token, name):
    created = await client.post(f"{API}/user/repos", json={"name": name, "auto_init": True}, headers=auth_headers(token))
    assert created.status_code in (200, 201), created.text
    return created.json()["id"]


async def _seed_actions(db_session, repo_id, actor_id):
    workflow = Workflow(repo_id=repo_id, name="fullsend", path=".github/workflows/fullsend.yaml")
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id, repo_id=repo_id, head_sha="a" * 40, head_branch="main",
        event="issues", status="completed", conclusion="success", run_number=1,
        run_attempt=1, actor_id=actor_id, created_at=datetime(2026, 9, 27, 15, 0),
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add_all([
        WorkflowJob(run_id=run.id, name="Triage", status="completed", conclusion="success"),
        WorkflowArtifact(run_id=run.id, repo_id=repo_id, name="fullsend-triage"),
        Secret(repo_id=repo_id, name="FULLSEND_GCP_PROJECT_ID", value="x"),
        Variable(repo_id=repo_id, name="FULLSEND_MODEL", value="haiku"),
    ])
    await db_session.commit()
    return run.id


async def _count(db_session, model, **where):
    stmt = select(func.count()).select_from(model)
    for key, value in where.items():
        stmt = stmt.where(getattr(model, key) == value)
    return (await db_session.execute(stmt)).scalar_one()


@pytest.mark.asyncio
async def test_deleting_a_repository_takes_its_actions_data_and_leaves_its_neighbour(client, db_session, test_user, test_token):
    doomed = await _create(client, test_token, "doomed")
    kept = await _create(client, test_token, "kept")
    await _seed_actions(db_session, doomed, test_user.id)
    kept_run = await _seed_actions(db_session, kept, test_user.id)

    deleted = await client.delete(f"{API}/repos/testuser/doomed", headers=auth_headers(test_token))
    assert deleted.status_code == 204, deleted.text
    assert not os.path.exists(os.path.join(settings.DATA_DIR, "repos", "testuser", "doomed.git"))

    for model in (Workflow, WorkflowRun, WorkflowArtifact, Secret, Variable):
        assert await _count(db_session, model, repo_id=doomed) == 0, model.__tablename__
        assert await _count(db_session, model, repo_id=kept) == 1, model.__tablename__
    assert await _count(db_session, WorkflowJob) == 1
    assert (await db_session.execute(select(WorkflowJob.run_id))).scalar_one() == kept_run
    # Nothing in the whole database names the deleted repository any more.
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if any(fk.column.table.name == "repositories" for fk in column.foreign_keys):
                left = (await db_session.execute(
                    select(func.count()).select_from(table).where(column == doomed)
                )).scalar_one()
                assert left == 0, f"{table.name}.{column.name} still names the deleted repository"


@pytest.mark.asyncio
async def test_a_deleted_repository_id_is_never_handed_out_again(client, db_session, test_user, test_token):
    first = await _create(client, test_token, "first")
    second = await _create(client, test_token, "second")
    assert second > first
    deleted = await client.delete(f"{API}/repos/testuser/second", headers=auth_headers(test_token))
    assert deleted.status_code == 204
    third = await _create(client, test_token, "third")
    assert third > second, "the deleted repository's id was reused"


def test_the_purge_plan_owns_by_repo_id_and_clears_pointers():
    """The plan is derived from the metadata: every table that references
    repositories is either deleted (owned through repo_id) or has its
    pointer cleared, and children are removed before their parents."""
    repositories = Base.metadata.tables["repositories"]
    plan = purge_statements(Base.metadata, select(repositories.c.id).where(repositories.c.id == 1))
    kinds = {}
    for statement in plan:
        kinds.setdefault(statement.table.name, set()).add(type(statement).__name__)
    assert kinds["repositories"] == {"Update", "Delete"}  # parent_id cleared, then the row
    assert "Update" in kinds["pull_requests"] and "Delete" in kinds["pull_requests"]  # head_repo_id, then own PRs
    for owner in ("workflows", "workflow_runs", "workflow_jobs", "workflow_artifacts", "secrets", "variables", "releases", "check_runs"):
        assert "Delete" in kinds[owner], owner
    order = [s.table.name for s in plan]
    assert order.index("workflow_jobs") < order.index("workflow_runs") < order.index("workflows")
    assert order[-1] == "repositories"
    # A job points at its runner; deleting a repository's runner clears that
    # pointer rather than taking every job that ran on it.
    runner_jobs = [s for s in plan if s.table.name == "workflow_jobs" and type(s).__name__ == "Update"]
    assert runner_jobs, "the job's runner pointer is cleared, not followed"


def test_the_orphan_plan_covers_every_owner_table():
    owners = {
        table.name for table in Base.metadata.sorted_tables
        for column in table.columns
        if column.name == "repo_id" and any(fk.column.table.name == "repositories" for fk in column.foreign_keys)
    }
    deleted = {s.table.name for s in orphan_purge_statements(Base.metadata) if type(s).__name__ == "Delete"}
    assert owners <= deleted
