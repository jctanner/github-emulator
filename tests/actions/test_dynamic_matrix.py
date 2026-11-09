"""Dynamic matrices: ``strategy.matrix: ${{ fromJSON(needs.x.outputs.matrix) }}``.

Fullsend's reusable dispatch computes its harness matrix in one job and fans
it out in the next. The emulator ran such a job once with an empty matrix,
so ``Harness run (${{ matrix.agent }})`` was named ``Harness run ()``, its
steps read empty ``matrix.*`` values ("repos input is required" from the
mint action), and every instance rendered the same concurrency group. On
GitHub the matrix is evaluated when the job's needs finish and the job is
expanded into one instance per combination.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.actions import WorkflowJob
from app.services.workflow_scheduler import check_run_completion, dispatch_ready_jobs
from app.services.workflow_service import (
    build_needs_context,
    matrix_combinations,
    matrix_job_name,
)
from app.services.workflow_service import create_workflow_run
from tests.actions.test_execution import executable_workflow  # noqa: F401 (fixture)

STEPS = [{"name": "Mint", "run": "echo ${{ matrix.agent }} ${{ matrix.status_repo }}"}]
HARNESS = {
    "name": "Harness run (${{ matrix.agent }})",
    "needs": ["harness-dispatch"],
    "if": "${{ !cancelled() && needs.harness-dispatch.outputs.matrix != '' }}",
    "strategy": {
        "fail-fast": False,
        "matrix": "${{ fromJSON(needs.harness-dispatch.outputs.matrix || '{\"include\":[]}') }}",
    },
    "concurrency": {
        "group": "fullsend-harness-${{ matrix.agent }}-${{ github.repository }}",
        "cancel-in-progress": True,
    },
    "runs-on": ["self-hosted"],
    "steps": STEPS,
}
TWO = '{"include":[{"agent":"pr-ping","status_repo":"o/r"},{"agent":"review","status_repo":"o/r"}]}'


async def _run(db_session, workflow, test_user, jobs):
    run = await create_workflow_run(
        db_session, workflow, {"name": "matrix", "jobs": jobs},
        event="push", payload={"ref": "refs/heads/main"}, actor=test_user,
        head_sha="matrix123", head_branch="main",
    )
    await db_session.commit()
    return run


async def _rows(db_session, run):
    return (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run.id).order_by(WorkflowJob.id)
    )).scalars().all()


async def _finish(db_session, job, conclusion, outputs=None):
    job.status = "completed"
    job.conclusion = conclusion
    job.completed_at = datetime.now(timezone.utc)
    job.outputs = outputs or {}
    await db_session.commit()


def _dispatch_job():
    return {"runs-on": ["self-hosted"], "steps": [{"run": "echo"}],
            "outputs": {"matrix": "${{ steps.d.outputs.matrix }}"}}


@pytest.mark.asyncio
async def test_the_matrix_expands_when_its_needs_finish(executable_workflow, db_session, test_user):  # noqa: F811
    _repo, workflow, _ = executable_workflow
    run = await _run(db_session, workflow, test_user, {
        "harness-dispatch": _dispatch_job(), "harness-run": HARNESS,
    })
    rows = await _rows(db_session, run)
    assert [r.job_key for r in rows] == ["harness-dispatch", "harness-run"]
    assert rows[1].concurrency_group is None
    await _finish(db_session, rows[0], "success", {"matrix": TWO})
    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()

    harness = [r for r in await _rows(db_session, run) if r.job_key == "harness-run"]
    assert sorted(r.name for r in harness) == ["Harness run (pr-ping)", "Harness run (review)"]
    assert all(r.status == "queued" for r in harness)
    groups = sorted(r.concurrency_group for r in harness)
    assert groups[0] != groups[1] and groups[0].startswith("fullsend-harness-pr-ping-")
    by_name = {r.name: r for r in harness}
    assert by_name["Harness run (review)"].steps[0]["run"] == "echo review o/r"
    assert by_name["Harness run (review)"].needs == ["harness-dispatch"]


@pytest.mark.asyncio
async def test_an_empty_matrix_fails_the_job(executable_workflow, db_session, test_user):  # noqa: F811
    _repo, workflow, _ = executable_workflow
    jobs = {"harness-dispatch": _dispatch_job(), "harness-run": {**HARNESS, "if": "${{ !cancelled() }}"}}
    run = await _run(db_session, workflow, test_user, jobs)
    rows = await _rows(db_session, run)
    await _finish(db_session, rows[0], "success", {"matrix": '{"include":[]}'})
    await dispatch_ready_jobs(db_session, run.id)
    await check_run_completion(db_session, run.id)
    await db_session.commit()
    rows = await _rows(db_session, run)
    assert [(r.status, r.conclusion) for r in rows if r.job_key == "harness-run"] == [("completed", "failure")]


@pytest.mark.asyncio
async def test_a_job_after_the_matrix_waits_for_every_instance(executable_workflow, db_session, test_user):  # noqa: F811
    _repo, workflow, _ = executable_workflow
    run = await _run(db_session, workflow, test_user, {
        "harness-dispatch": _dispatch_job(),
        "harness-run": HARNESS,
        "report": {"needs": ["harness-run"], "runs-on": ["self-hosted"], "steps": [{"run": "echo"}]},
    })
    rows = await _rows(db_session, run)
    await _finish(db_session, rows[0], "success", {"matrix": TWO})
    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()

    harness = [r for r in await _rows(db_session, run) if r.job_key == "harness-run"]
    await _finish(db_session, harness[0], "success")
    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()
    report = next(r for r in await _rows(db_session, run) if r.job_key == "report")
    assert report.status == "waiting"

    await _finish(db_session, harness[1], "failure")
    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()
    report = next(r for r in await _rows(db_session, run) if r.job_key == "report")
    assert report.conclusion == "skipped"


@pytest.mark.asyncio
async def test_a_matrix_without_needs_expands_at_creation(executable_workflow, db_session, test_user):  # noqa: F811
    _repo, workflow, _ = executable_workflow
    run = await _run(db_session, workflow, test_user, {
        "build": {
            "name": "Build",
            "strategy": {"matrix": "${{ fromJSON('{\"os\":[\"linux\",\"mac\"]}') }}"},
            "runs-on": ["self-hosted"], "steps": [{"run": "echo ${{ matrix.os }}"}],
        },
    })
    rows = await _rows(db_session, run)
    assert sorted(r.name for r in rows) == ["Build (linux)", "Build (mac)"]
    assert sorted(r.steps[0]["run"] for r in rows) == ["echo linux", "echo mac"]


def test_matrix_combinations_follow_github():
    assert matrix_combinations({"include": []}) == []
    assert matrix_combinations({"include": [{"a": 1}, {"a": 2}]}) == [{"a": 1}, {"a": 2}]
    assert matrix_combinations({"os": ["l", "m"], "exclude": [{"os": "m"}]}) == [{"os": "l"}]
    # An include extends the combinations it does not contradict and adds
    # itself when it extends none.
    assert matrix_combinations({
        "os": ["l", "m"],
        "include": [{"os": "l", "extra": 1}, {"os": "w"}, {"color": "g"}],
    }) == [{"os": "l", "extra": 1, "color": "g"}, {"os": "m", "color": "g"}, {"os": "w"}]
    assert matrix_combinations("not a matrix") is None


def test_matrix_job_names():
    assert matrix_job_name("Build", {"os": "l", "v": 3}) == "Build (l, 3)"
    assert matrix_job_name("Run (${{ matrix.agent }})", {"agent": "x"}) == "Run (${{ matrix.agent }})"
    assert matrix_job_name("Build", {}) == "Build"


def test_needs_reports_the_worst_instance():
    class Row:
        def __init__(self, conclusion, outputs):
            self.job_key, self.name, self.conclusion, self.outputs = "dispatch / run", "x", conclusion, outputs

    context = build_needs_context([Row("success", {"a": "1"}), Row("failure", {"b": "2"})])
    assert context["dispatch / run"] == {"outputs": {"a": "1", "b": "2"}, "result": "failure"}
    assert context["run"] == context["dispatch / run"]


@pytest.mark.asyncio
async def test_a_job_after_a_failed_expansion_is_decided(executable_workflow, db_session, test_user):  # noqa: F811
    _repo, workflow, _ = executable_workflow
    run = await _run(db_session, workflow, test_user, {
        "harness-dispatch": _dispatch_job(),
        "harness-run": {**HARNESS, "if": "${{ !cancelled() }}"},
        "cleanup": {"needs": ["harness-run"], "if": "always()", "runs-on": ["self-hosted"], "steps": [{"run": "echo"}]},
        "report": {"needs": ["harness-run"], "runs-on": ["self-hosted"], "steps": [{"run": "echo"}]},
    })
    rows = await _rows(db_session, run)
    await _finish(db_session, rows[0], "success", {"matrix": '{"include":[]}'})
    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()
    by_key = {r.job_key: r for r in await _rows(db_session, run)}
    assert by_key["harness-run"].conclusion == "failure"
    assert by_key["cleanup"].status == "queued"
    assert by_key["report"].conclusion == "skipped"


@pytest.mark.asyncio
async def test_an_empty_matrix_without_needs_fails_the_job(executable_workflow, db_session, test_user):  # noqa: F811
    _repo, workflow, _ = executable_workflow
    run = await _run(db_session, workflow, test_user, {
        "build": {
            "strategy": {"matrix": "${{ fromJSON('{\"include\":[]}') }}"},
            "runs-on": ["self-hosted"], "steps": [{"run": "echo"}],
        },
    })
    rows = await _rows(db_session, run)
    assert [(r.status, r.conclusion) for r in rows] == [("completed", "failure")]


@pytest.mark.asyncio
async def test_runner_labels_read_the_matrix(executable_workflow, db_session, test_user):  # noqa: F811
    _repo, workflow, _ = executable_workflow
    run = await _run(db_session, workflow, test_user, {
        "harness-dispatch": _dispatch_job(),
        "harness-run": {**HARNESS, "runs-on": "${{ matrix.agent }}"},
        "static": {
            "strategy": {"matrix": {"os": ["linux"]}},
            "runs-on": ["self-hosted", "${{ matrix.os }}"], "steps": [{"run": "echo"}],
        },
    })
    rows = await _rows(db_session, run)
    static = next(r for r in rows if r.job_key == "static")
    assert static.labels == ["self-hosted", "linux"]
    await _finish(db_session, rows[0], "success", {"matrix": TWO})
    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()
    harness = [r for r in await _rows(db_session, run) if r.job_key == "harness-run"]
    assert sorted(label for r in harness for label in r.labels) == ["pr-ping", "review"]


@pytest.mark.asyncio
async def test_each_instance_knows_its_strategy_index(executable_workflow, db_session, test_user):  # noqa: F811
    """Fullsend's "Export dispatch context env" step fails with "matrix agent
    and job index must be valid" when ``strategy.job-index`` is empty, and
    reads every agent's name through the object filter ``include.*.agent``."""
    _repo, workflow, _ = executable_workflow
    steps = [{"name": "Export", "env": {
        "MATRIX_JOB_INDEX": "${{ strategy.job-index }}",
        "MATRIX_JOB_TOTAL": "${{ strategy.job-total }}",
        "MATRIX_AGENT_NAMES": "${{ toJSON(fromJSON(needs.harness-dispatch.outputs.matrix).include.*.agent) }}",
    }, "run": "echo"}]
    run = await _run(db_session, workflow, test_user, {
        "harness-dispatch": _dispatch_job(),
        "harness-run": {**HARNESS, "steps": steps},
        "static": {"strategy": {"matrix": {"os": ["l", "m"]}}, "runs-on": ["self-hosted"],
                   "steps": [{"run": "echo ${{ strategy.job-index }}/${{ strategy.job-total }}"}]},
    })
    rows = await _rows(db_session, run)
    assert sorted(r.steps[0]["run"] for r in rows if r.job_key == "static") == ["echo 0/2", "echo 1/2"]
    await _finish(db_session, rows[0], "success", {"matrix": TWO})
    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()
    harness = {r.name: r for r in await _rows(db_session, run) if r.job_key == "harness-run"}

    def env(name):
        return harness[name].steps[0]["env"]

    assert env("Harness run (pr-ping)")["MATRIX_JOB_INDEX"] == "0"
    assert env("Harness run (review)")["MATRIX_JOB_INDEX"] == "1"
    assert env("Harness run (review)")["MATRIX_JOB_TOTAL"] == "2"
    assert env("Harness run (review)")["MATRIX_AGENT_NAMES"] == '["pr-ping","review"]'


@pytest.mark.asyncio
async def test_a_skipped_job_with_an_empty_matrix_is_skipped(executable_workflow, db_session, test_user):  # noqa: F811
    _repo, workflow, _ = executable_workflow
    run = await _run(db_session, workflow, test_user, {
        "build": {
            "if": "false",
            "strategy": {"matrix": "${{ fromJSON('{\"include\":[]}') }}"},
            "runs-on": ["self-hosted"], "steps": [{"run": "echo"}],
        },
    })
    rows = await _rows(db_session, run)
    assert [(r.status, r.conclusion) for r in rows] == [("completed", "skipped")]


def test_strategy_options_may_be_expressions():
    from app.services.workflow_service import strategy_context

    ctx = {"inputs": {"fast": False, "workers": "3"}}
    strategy = {"fail-fast": "${{ inputs.fast }}", "max-parallel": "${{ inputs.workers }}"}
    assert strategy_context(strategy, 1, 4, ctx) == {
        "fail-fast": False, "job-index": 1, "job-total": 4, "max-parallel": 3,
    }
    assert strategy_context({}, 0, 2)["fail-fast"] is True


@pytest.mark.asyncio
async def test_a_matrix_job_skipped_before_it_expands_keeps_its_unrendered_name(
    executable_workflow, db_session, test_user,  # noqa: F811
):
    """GitHub names a matrix job that never expanded by its template.

    Fullsend's behaviour suite reads ``Harness run (${{ matrix.agent }})`` as
    "no harness scheduled". The emulator rendered it against an empty matrix,
    so a skipped job read ``Harness run ()``: an instance that was scheduled.
    """
    _repo, workflow, _ = executable_workflow
    run = await _run(db_session, workflow, test_user, {
        "harness-dispatch": _dispatch_job(), "harness-run": HARNESS,
    })
    rows = await _rows(db_session, run)
    [harness] = [r for r in rows if r.job_key == "harness-run"]
    assert harness.name == "Harness run (${{ matrix.agent }})"

    dispatch = next(r for r in rows if r.job_key == "harness-dispatch")
    await _finish(db_session, dispatch, "success", {"matrix": ""})
    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()
    [harness] = [r for r in await _rows(db_session, run) if r.job_key == "harness-run"]
    assert (harness.status, harness.conclusion) == ("completed", "skipped")
    assert harness.name == "Harness run (${{ matrix.agent }})"
