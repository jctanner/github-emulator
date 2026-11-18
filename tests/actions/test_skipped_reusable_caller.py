"""A skipped caller of a reusable workflow, and runs whose jobs all skipped.

On GitHub a job that calls a reusable workflow (``uses: ./.github/workflows/
x.yml``) and is skipped by its own ``if:`` is never expanded: the run lists
one job, named after the caller job, concluded ``skipped``. A run whose jobs
were all skipped concludes ``skipped``, not ``success``.

The emulator inlined the called jobs, ANDed the caller's condition into each
of them and skipped them one by one, so the Jobs API listed every called job
(``dispatch / route``, ``Triage``, ``Harness run (${{ matrix.agent }})``...)
as skipped, and the run concluded ``success``. Fullsend's behaviour tests
skip runs that concluded ``skipped`` and read a ``success`` run with a
skipped "Harness run" job as a failed dispatch.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.actions import WorkflowJob, WorkflowRun
from app.services import workflow_service
from app.services.workflow_scheduler import check_run_completion, dispatch_ready_jobs
from app.services.workflow_service import create_workflow_run
from tests.actions.test_execution import executable_workflow  # noqa: F401 (fixture)

GO = "github.event_name == 'issues' && github.event.label.name == 'go'"


def _harness(need):
    return {
        "name": "Harness run (${{ matrix.agent }})",
        "needs": [need],
        "if": f"${{{{ !cancelled() && needs.{need}.outputs.matrix != '' }}}}",
        "strategy": {"matrix": f"${{{{ fromJSON(needs.{need}.outputs.matrix || '{{\"include\":[]}}') }}}}"},
        "runs-on": ["self-hosted"],
        "steps": [{"run": "echo ${{ matrix.agent }}"}],
    }


def _stage(name):
    return {
        "name": name,
        "needs": ["route"],
        "if": "needs.route.outputs.stage == '" + name.lower() + "'",
        "runs-on": ["self-hosted"],
        "outputs": {"matrix": "${{ steps.m.outputs.matrix }}"},
        "steps": [{"id": "m", "run": "echo"}],
    }


CALLED = {
    "on": {"workflow_call": {}},
    "jobs": {
        "route": {
            "runs-on": ["self-hosted"],
            "outputs": {"stage": "${{ steps.r.outputs.stage }}"},
            "steps": [{"id": "r", "run": "echo"}],
        },
        "triage": _stage("Triage"),
        "code": _stage("Code"),
        "harness-run-triage": _harness("triage"),
        "harness-run-code": _harness("code"),
    },
}


def _caller(**extra):
    return {
        "name": "fullsend",
        "jobs": {
            "dispatch": {
                "if": GO,
                "uses": "./.github/workflows/reusable-dispatch.yml",
                **extra,
            },
        },
    }


async def _materialize(monkeypatch, caller):
    async def fake_detect(path, ref="HEAD"):
        return [{"_path": ".github/workflows/reusable-dispatch.yml", **CALLED}]

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    return await workflow_service.materialize_reusable_workflows(
        caller, "caller.git", "main", None,
    )


async def _run(db_session, workflow, test_user, workflow_yaml, payload, event="issues"):
    run = await create_workflow_run(
        db_session, workflow, workflow_yaml, event=event,
        payload={"repository": {"full_name": "testuser/actions-exec-repo"}, **payload},
        actor=test_user, head_sha="caller123", head_branch="main",
    )
    await db_session.commit()
    return run


async def _rows(db_session, run):
    return (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run.id).order_by(WorkflowJob.id)
    )).scalars().all()


async def _reload(db_session, run):
    return (await db_session.execute(
        select(WorkflowRun).where(WorkflowRun.id == run.id)
    )).scalar_one()


@pytest.mark.asyncio
async def test_a_skipped_caller_is_one_skipped_job_and_the_run_is_skipped(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    _repo, workflow, _ = executable_workflow
    workflow_yaml = await _materialize(monkeypatch, _caller())
    run = await _run(db_session, workflow, test_user, workflow_yaml, {"action": "opened"})

    rows = await _rows(db_session, run)
    assert [(r.name, r.job_key, r.status, r.conclusion) for r in rows] == [
        ("dispatch", "dispatch", "completed", "skipped"),
    ]
    run = await _reload(db_session, run)
    assert (run.status, run.conclusion) == ("completed", "skipped")


@pytest.mark.asyncio
async def test_a_skipped_caller_is_listed_under_its_name(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    _repo, workflow, _ = executable_workflow
    workflow_yaml = await _materialize(monkeypatch, _caller(name="Dispatch"))
    run = await _run(db_session, workflow, test_user, workflow_yaml, {"action": "opened"})

    assert [r.name for r in await _rows(db_session, run)] == ["Dispatch"]


@pytest.mark.asyncio
async def test_a_job_that_needs_a_skipped_caller_is_skipped(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    _repo, workflow, _ = executable_workflow
    caller = _caller()
    caller["jobs"]["report"] = {
        "needs": ["dispatch"], "runs-on": ["self-hosted"], "steps": [{"run": "echo"}],
    }
    workflow_yaml = await _materialize(monkeypatch, caller)
    run = await _run(db_session, workflow, test_user, workflow_yaml, {"action": "opened"})

    rows = await _rows(db_session, run)
    assert [(r.job_key, r.conclusion) for r in rows] == [
        ("dispatch", "skipped"), ("report", "skipped"),
    ]
    assert (await _reload(db_session, run)).conclusion == "skipped"


@pytest.mark.asyncio
async def test_a_caller_that_runs_keeps_the_called_job_names(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    _repo, workflow, _ = executable_workflow
    workflow_yaml = await _materialize(monkeypatch, _caller())
    run = await _run(
        db_session, workflow, test_user, workflow_yaml,
        {"action": "labeled", "label": {"name": "go"}},
    )

    rows = await _rows(db_session, run)
    assert [r.name for r in rows] == [
        "dispatch / route", "Triage", "Code",
        "Harness run (${{ matrix.agent }})", "Harness run (${{ matrix.agent }})",
    ]
    assert rows[0].status == "queued"
    assert all(r.status == "waiting" for r in rows[1:])
    run = await _reload(db_session, run)
    assert run.conclusion is None


async def _finish(db_session, job, conclusion):
    job.status = "completed"
    job.conclusion = conclusion
    job.completed_at = datetime.now(timezone.utc)
    await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("ran, expected", [("success", "success"), ("failure", "failure")])
async def test_a_run_with_some_skipped_jobs_keeps_its_conclusion(
    executable_workflow, db_session, test_user, ran, expected,  # noqa: F811
):
    _repo, workflow, _ = executable_workflow
    run = await _run(db_session, workflow, test_user, {"jobs": {
        "build": {"runs-on": ["self-hosted"], "steps": [{"run": "echo"}]},
        "deploy": {"if": "false", "runs-on": ["self-hosted"], "steps": [{"run": "echo"}]},
    }}, {"ref": "refs/heads/main"}, event="push")
    rows = await _rows(db_session, run)
    assert [r.conclusion for r in rows] == [None, "skipped"]

    await _finish(db_session, rows[0], ran)
    await dispatch_ready_jobs(db_session, run.id)
    await check_run_completion(db_session, run.id)
    await db_session.commit()
    assert (await _reload(db_session, run)).conclusion == expected


@pytest.mark.asyncio
async def test_a_run_whose_jobs_all_skipped_is_skipped(
    executable_workflow, db_session, test_user,  # noqa: F811
):
    _repo, workflow, _ = executable_workflow
    run = await _run(db_session, workflow, test_user, {"jobs": {
        "build": {"if": "false", "runs-on": ["self-hosted"], "steps": [{"run": "echo"}]},
        "test": {"needs": ["build"], "runs-on": ["self-hosted"], "steps": [{"run": "echo"}]},
    }}, {"ref": "refs/heads/main"}, event="push")

    rows = await _rows(db_session, run)
    assert [r.conclusion for r in rows] == ["skipped", "skipped"]
    run = await _reload(db_session, run)
    assert (run.status, run.conclusion) == ("completed", "skipped")
