"""A reusable-workflow call that cannot be inlined fails, with the reason.

It used to become a job with no steps, which passed: a run whose whole
pipeline was one call to a repository the emulator did not hold looked green.
"""

import subprocess

import pytest

from app.services import workflow_service


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
    # It is a job of its own that a runner picks up, not one waiting forever.
    [labels] = [j["runs_on"] for j in workflow_service.build_job_graph({"jobs": jobs})]
    assert labels == ["ubuntu-latest"]


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
