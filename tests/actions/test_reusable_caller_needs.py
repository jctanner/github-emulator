"""A job that needs a reusable-workflow caller waits for the call and reads it.

The emulator inlines the called jobs as ``<caller> / <job>`` and the caller
itself is no job in the run, so a job with ``needs: <caller>`` waited for a
job that never existed and stayed ``waiting`` for good. And
``needs.<caller>.outputs.*``, which GitHub fills from the called workflow's
``on.workflow_call.outputs``, had nothing to read.
"""

import pytest
from sqlalchemy import select

from app.models.actions import WorkflowJob
from app.services import workflow_service
from app.services.workflow_scheduler import dispatch_ready_jobs
from app.services.workflow_service import create_workflow_run, needs_alias_entries
from tests.actions.test_execution import executable_workflow  # noqa: F401 (fixture)

CALLED = {
    "on": {"workflow_call": {"outputs": {
        "got": {"description": "from inner", "value": "${{ jobs.inner.outputs.got }}"},
        "both": {"value": "${{ jobs.inner.outputs.got }}-${{ jobs.second.outputs.extra }}"},
    }}},
    "jobs": {
        "inner": {
            "runs-on": ["self-hosted"],
            "outputs": {"got": "${{ steps.s.outputs.got }}"},
            "steps": [{"id": "s", "run": "echo"}],
        },
        "second": {
            "runs-on": ["self-hosted"],
            "outputs": {"extra": "${{ steps.s.outputs.extra }}"},
            "steps": [{"id": "s", "run": "echo"}],
        },
    },
}

CALLER = {
    "name": "caller",
    "jobs": {
        "call": {"uses": "./.github/workflows/called.yml"},
        "after": {
            "needs": ["call"],
            "runs-on": ["self-hosted"],
            "steps": [{"run": "echo ${{ needs.call.outputs.got }} ${{ needs.call.outputs.both }} ${{ needs.call.result }}"}],
        },
    },
}


async def _materialize(monkeypatch, caller):
    async def fake_detect(path, ref="HEAD"):
        return [{"_path": ".github/workflows/called.yml", **CALLED}]

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    return await workflow_service.materialize_reusable_workflows(caller, "caller.git", "main", None)


@pytest.mark.asyncio
async def test_needs_on_a_caller_means_needs_on_every_called_job(monkeypatch):
    jobs = (await _materialize(monkeypatch, CALLER))["jobs"]
    assert sorted(jobs) == ["after", "call / inner", "call / second"]
    assert jobs["after"]["needs"] == ["call / inner", "call / second"]
    assert jobs["after"]["_needs_aliases"]["call"]["jobs"] == {
        "inner": "call / inner", "second": "call / second",
    }


@pytest.mark.asyncio
async def test_the_dependent_runs_with_the_calls_outputs_and_result(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    _repo, workflow, _ = executable_workflow
    workflow_yaml = await _materialize(monkeypatch, CALLER)
    run = await create_workflow_run(
        db_session, workflow, workflow_yaml, event="push",
        payload={"repository": {"full_name": "testuser/actions-exec-repo"}},
        actor=test_user, head_sha="caller123", head_branch="main",
    )
    await db_session.commit()

    rows = {
        r.job_key: r for r in (await db_session.execute(
            select(WorkflowJob).where(WorkflowJob.run_id == run.id)
        )).scalars().all()
    }
    assert rows["after"].status == "waiting"
    for key, outputs in (("call / inner", {"got": "value-x"}), ("call / second", {"extra": "y"})):
        rows[key].status = "completed"
        rows[key].conclusion = "success"
        rows[key].outputs = outputs
    await db_session.commit()

    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()
    after = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run.id, WorkflowJob.job_key == "after")
    )).scalar_one()
    assert after.status == "queued"
    assert after.steps[0]["run"] == "echo value-x value-x-y success"


def test_the_call_result_is_the_worst_of_its_jobs():
    aliases = {"call": {"jobs": {"a": "call / a", "b": "call / b"}, "outputs": {}}}
    needs = {
        "call / a": {"result": "success", "outputs": {}},
        "call / b": {"result": "failure", "outputs": {}},
    }
    assert needs_alias_entries(aliases, needs)["call"]["result"] == "failure"
