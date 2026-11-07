"""A local reusable workflow is named by the repository that calls it.

``uses: ./.github/workflows/x.yml`` names no repository, and the emulator left
the called repository empty. The job's ``job_workflow_ref`` then read
``/.github/workflows/x.yml@refs/heads/main`` and its workflow source was blank.
GitHub reports ``owner/repo/.github/workflows/x.yml@refs/heads/main``.
Fullsend's mint keys on that repository, so it refused every vendored
install, whose dispatch workflow is local to the installed repository.
"""

import pytest
from sqlalchemy import select

from app.models.repository import Repository
from app.services import workflow_service


@pytest.mark.asyncio
async def test_local_call_reports_the_calling_repository(db_session, test_repo_with_init, monkeypatch):
    owner, name, _ = test_repo_with_init
    repository = (
        await db_session.execute(select(Repository).where(Repository.full_name == f"{owner}/{name}"))
    ).scalar_one()

    async def fake_detect(path, ref="HEAD"):
        assert path == repository.disk_path
        return [{
            "_path": ".github/workflows/reusable-dispatch.yml",
            "jobs": {"route": {"runs-on": "fullsend", "steps": [{"run": "echo route"}]}},
        }]

    async def fake_sha(_path, _ref):
        return "b" * 40

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    monkeypatch.setattr(workflow_service, "_resolve_ref_sha", fake_sha)

    result = await workflow_service.materialize_reusable_workflows(
        {"jobs": {"dispatch": {"uses": "./.github/workflows/reusable-dispatch.yml"}}},
        repository.disk_path,
        "main",
        db_session,
    )

    job = result["jobs"]["dispatch / route"]
    assert job["_workflow_ref"] == (
        f"{owner}/{name}/.github/workflows/reusable-dispatch.yml@refs/heads/main"
    )
    assert job["_workflow_repository"] == f"{owner}/{name}"
    assert job["_workflow_sha"] == "b" * 40


@pytest.mark.asyncio
async def test_local_call_without_a_database_still_resolves(monkeypatch):
    """Without a database the call still expands; it just cannot be named."""
    async def fake_detect(_path, ref="HEAD"):
        return [{"_path": ".github/workflows/agent.yml", "jobs": {"run": {"steps": [{"run": "true"}]}}}]

    async def fake_sha(_path, _ref):
        return "c" * 40

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    monkeypatch.setattr(workflow_service, "_resolve_ref_sha", fake_sha)
    result = await workflow_service.materialize_reusable_workflows(
        {"jobs": {"agent": {"uses": "./.github/workflows/agent.yml"}}}, "caller.git", "main"
    )
    assert list(result["jobs"]) == ["agent / run"]
