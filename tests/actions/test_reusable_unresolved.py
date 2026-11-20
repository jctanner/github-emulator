"""A reusable-workflow call that cannot be inlined fails, with the reason.

It used to become a job with no steps, which passed: a run whose whole
pipeline was one call to a repository the emulator did not hold looked green.
GitHub fails such a job without assigning a runner, so it is concluded here
when it would start, not queued for a runner that may never offer its labels.
"""

import os
import subprocess
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.actions import WorkflowJob, WorkflowRun
from app.services import workflow_service
from app.services.workflow_scheduler import check_run_completion, dispatch_ready_jobs
from app.services.workflow_service import create_workflow_run
from tests.actions.test_execution import executable_workflow  # noqa: F401 (fixture)

MISSING = "elsewhere/tools/.github/workflows/dispatch.yml@main"


class _NoRepository:
    def scalar_one_or_none(self):
        return None


class _EmptyDB:
    async def execute(self, _query):
        return _NoRepository()


async def _materialize(jobs: dict, db=None) -> dict:
    return (await workflow_service.materialize_reusable_workflows(
        {"jobs": jobs}, "caller.git", "main", db or _EmptyDB(),
    ))["jobs"]


def _assert_refused(job: dict, phrase: str):
    [step] = job["steps"]
    assert "uses" not in job
    result = subprocess.run(["bash", "-c", step["run"]], capture_output=True, text=True)
    assert result.returncode == 1
    assert "::error::" in result.stderr and phrase in result.stderr


@pytest.mark.asyncio
async def test_a_call_to_a_repository_not_on_the_emulator_fails():
    jobs = await _materialize({
        "dispatch": {
            "uses": "elsewhere/tools/.github/workflows/dispatch.yml@main",
            "with": {"x": "1"},
        },
    })
    job = jobs["dispatch"]
    assert job["name"] == "dispatch (reusable workflow)"
    _assert_refused(job, "elsewhere/tools/.github/workflows/dispatch.yml@main was not found")
    assert "was not found on this emulator" in job["_refused"]


async def _rows(db_session, run):
    return {
        r.job_key: r for r in (await db_session.execute(
            select(WorkflowJob).where(WorkflowJob.run_id == run.id)
        )).scalars().all()
    }


async def _start(db_session, workflow, test_user, jobs):
    run = await create_workflow_run(
        db_session, workflow, {"jobs": await _materialize(jobs)}, event="push",
        payload={"repository": {"full_name": "testuser/actions-exec-repo"}},
        actor=test_user, head_sha="caller123", head_branch="main",
    )
    await db_session.commit()
    return run


def _assert_failed_without_a_runner(job):
    assert (job.status, job.conclusion) == ("completed", "failure")
    assert job.runner_id is None
    assert [(s["name"], s["conclusion"]) for s in job.steps] == [
        ("Resolve reusable workflow", "failure"),
    ]
    with open(os.path.join(settings.DATA_DIR, "logs", "jobs", f"{job.id}.log")) as log:
        assert "was not found on this emulator" in log.read()


@pytest.mark.asyncio
async def test_an_unresolved_call_fails_the_run_without_a_runner(
    executable_workflow, db_session, test_user,  # noqa: F811
):
    _repo, workflow, _ = executable_workflow
    run = await _start(db_session, workflow, test_user, {"dispatch": {"uses": MISSING}})

    rows = await _rows(db_session, run)
    _assert_failed_without_a_runner(rows["dispatch"])
    run = (await db_session.execute(select(WorkflowRun).where(WorkflowRun.id == run.id))).scalar_one()
    assert (run.status, run.conclusion) == ("completed", "failure")


@pytest.mark.asyncio
async def test_an_unresolved_call_with_needs_fails_when_its_needs_finish(
    executable_workflow, db_session, test_user,  # noqa: F811
):
    _repo, workflow, _ = executable_workflow
    run = await _start(db_session, workflow, test_user, {
        "build": {"runs-on": ["self-hosted"], "steps": [{"run": "echo"}]},
        "dispatch": {"needs": ["build"], "uses": MISSING},
    })
    rows = await _rows(db_session, run)
    assert rows["dispatch"].status == "waiting"

    rows["build"].status = "completed"
    rows["build"].conclusion = "success"
    rows["build"].completed_at = datetime.now(timezone.utc)
    await db_session.commit()
    await dispatch_ready_jobs(db_session, run.id)
    await check_run_completion(db_session, run.id)
    await db_session.commit()

    rows = await _rows(db_session, run)
    _assert_failed_without_a_runner(rows["dispatch"])
    run = (await db_session.execute(select(WorkflowRun).where(WorkflowRun.id == run.id))).scalar_one()
    assert (run.status, run.conclusion) == ("completed", "failure")


@pytest.mark.asyncio
async def test_a_call_that_loops_back_to_itself_fails(monkeypatch):
    loop = "org/repo/.github/workflows/loop.yml@main"

    async def fake_detect(path, ref="HEAD"):
        return [{"_path": ".github/workflows/loop.yml", "jobs": {"again": {"uses": loop}}}]

    class Repository:
        disk_path = "repo.git"
        full_name = "org/repo"

    class Found:
        def scalar_one_or_none(self):
            return Repository()

    class DB:
        async def execute(self, _query):
            return Found()

    async def fake_sha(path, ref):
        return "0" * 40

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    monkeypatch.setattr(workflow_service, "_resolve_ref_sha", fake_sha)
    jobs = await _materialize({"start": {"uses": loop}}, DB())
    _assert_refused(jobs["start / again"], "call themselves")
