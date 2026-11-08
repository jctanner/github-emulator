"""Job status functions and Actions comparison semantics in job conditions.

Fullsend's reusable dispatch gates its harness job on
``!cancelled() && (... fromJSON(needs.harness-dispatch.outputs.matrix).include[0] != null)``.
The evaluator raised "unsupported function cancelled", which counted as
false, so the job was always skipped (BT-U1). On GitHub:

- ``success()``, ``failure()``, ``cancelled()`` and ``always()`` are job
  status functions;
- a condition without one is ``success() && (...)``, so a job whose need
  failed or was skipped is skipped; one that names a status function is
  evaluated anyway;
- ``==`` compares strings case-insensitively and other mixed types as
  numbers (``null == ''``), and ``<``, ``>``, ``<=``, ``>=`` exist.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.actions import WorkflowJob
from app.services.workflow_expressions import evaluate_job_if, uses_status_function
from app.services.workflow_scheduler import dispatch_ready_jobs
from app.services.workflow_service import create_workflow_run
from tests.actions.test_execution import executable_workflow  # noqa: F401 (fixture)

HARNESS_IF = (
    "${{ !cancelled() && ((inputs.matrix != '' && fromJSON(inputs.matrix).include[0] != null) || "
    "(needs.harness-dispatch.outputs.matrix != '' && "
    "fromJSON(needs.harness-dispatch.outputs.matrix).include[0] != null)) }}"
)
MATRIX = '{"include":[{"agent":"pr-ping"}]}'


def _ctx(result="success", matrix=MATRIX, inputs=None):
    return {
        "inputs": inputs if inputs is not None else {"matrix": ""},
        "needs": {"harness-dispatch": {"result": result, "outputs": {"matrix": matrix}}},
        "__job_needs__": ["harness-dispatch"],
    }


def test_the_harness_condition_runs_with_a_matrix():
    assert evaluate_job_if(HARNESS_IF, _ctx()) is True
    assert evaluate_job_if(HARNESS_IF, _ctx(matrix='{"include":[]}')) is False
    assert evaluate_job_if(HARNESS_IF, _ctx(matrix="")) is False
    # An input left unset reads as null, which equals '' in Actions.
    assert evaluate_job_if(HARNESS_IF, _ctx(inputs={})) is True


def test_status_functions():
    assert evaluate_job_if("success()", _ctx()) is True
    assert evaluate_job_if("success()", _ctx(result="failure")) is False
    assert evaluate_job_if("success()", _ctx(result="skipped")) is False
    assert evaluate_job_if("failure()", _ctx(result="failure")) is True
    assert evaluate_job_if("failure()", _ctx()) is False
    assert evaluate_job_if("always()", _ctx(result="failure")) is True
    assert evaluate_job_if("!cancelled()", _ctx(result="failure")) is True
    assert evaluate_job_if("cancelled()", {**_ctx(), "__run_cancelled__": True}) is True
    assert evaluate_job_if("success()", {**_ctx(), "__run_cancelled__": True}) is False
    # Only the job's own needs count.
    ctx = _ctx()
    ctx["needs"]["unrelated"] = {"result": "failure", "outputs": {}}
    assert evaluate_job_if("success()", ctx) is True
    assert uses_status_function("${{ !cancelled() && x }}")
    assert not uses_status_function("github.event.action == 'cancelled'")


@pytest.mark.parametrize("expression, expected", [
    ("'ABC' == 'abc'", True),
    ("'abc' != 'ABD'", True),
    ("null == ''", True),
    ("null == 0", True),
    ("'1' == 1", True),
    ("true == 1", True),
    ("'x' == 0", False),
    ("'x' != 0", True),
    ("2 > 1", True),
    ("1 >= 1", True),
    ("'2' < 10", True),
    ("1 <= 0", False),
    ("contains('Hello World', 'WORLD')", True),
    ("startsWith('Ready-to-code', 'ready-')", True),
    ("endsWith('feature.YML', '.yml')", True),
    ("contains(fromJSON('[\"A\",\"b\"]'), 'a')", True),
])
def test_comparisons_follow_actions(expression, expected):
    assert evaluate_job_if(expression, {}) is expected


async def _run_with(db_session, workflow, test_user, jobs):
    run = await create_workflow_run(
        db_session, workflow, {"name": "status", "jobs": jobs},
        event="push", payload={"ref": "refs/heads/main"}, actor=test_user,
        head_sha="status123", head_branch="main",
    )
    await db_session.commit()
    return run


async def _jobs(db_session, run):
    rows = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.run_id == run.id))).scalars().all()
    return {row.job_key or row.name: row for row in rows}


async def _finish(db_session, job, conclusion, outputs=None):
    job.status = "completed"
    job.conclusion = conclusion
    job.completed_at = datetime.now(timezone.utc)
    job.outputs = outputs or {}
    await db_session.commit()


@pytest.mark.asyncio
async def test_a_job_naming_a_status_function_runs_after_a_failed_need(executable_workflow, db_session, test_user):  # noqa: F811
    _repo, workflow, _run = executable_workflow
    steps = [{"run": "echo"}]
    run = await _run_with(db_session, workflow, test_user, {
        "route": {"runs-on": ["self-hosted"], "steps": steps},
        "plain": {"needs": "route", "runs-on": ["self-hosted"], "steps": steps},
        "on-failure": {"needs": "route", "if": "failure()", "runs-on": ["self-hosted"], "steps": steps},
        "always": {"needs": "route", "if": "${{ always() }}", "runs-on": ["self-hosted"], "steps": steps},
        "on-success": {"needs": "route", "if": "success()", "runs-on": ["self-hosted"], "steps": steps},
    })
    jobs = await _jobs(db_session, run)
    await _finish(db_session, jobs["route"], "failure")
    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()
    jobs = await _jobs(db_session, run)
    assert jobs["plain"].conclusion == "skipped"
    assert jobs["on-success"].conclusion == "skipped"
    assert jobs["on-failure"].status == "queued"
    assert jobs["always"].status == "queued"


@pytest.mark.asyncio
async def test_the_harness_run_job_is_queued(executable_workflow, db_session, test_user):  # noqa: F811
    _repo, workflow, _run = executable_workflow
    steps = [{"run": "echo"}]
    run = await _run_with(db_session, workflow, test_user, {
        "harness-dispatch": {"runs-on": ["self-hosted"], "steps": steps,
                             "outputs": {"matrix": "${{ steps.d.outputs.matrix }}"}},
        "harness-run": {"needs": ["harness-dispatch"], "if": HARNESS_IF, "runs-on": ["self-hosted"], "steps": steps},
    })
    jobs = await _jobs(db_session, run)
    await _finish(db_session, jobs["harness-dispatch"], "success", {"matrix": MATRIX})
    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()
    jobs = await _jobs(db_session, run)
    assert jobs["harness-run"].status == "queued", jobs["harness-run"].conclusion


@pytest.mark.asyncio
async def test_failure_sees_a_failed_ancestor_behind_a_skipped_job(executable_workflow, db_session, test_user):  # noqa: F811
    _repo, workflow, _run = executable_workflow
    steps = [{"run": "echo"}]
    run = await _run_with(db_session, workflow, test_user, {
        "build": {"runs-on": ["self-hosted"], "steps": steps},
        "test": {"needs": "build", "runs-on": ["self-hosted"], "steps": steps},
        "cleanup": {"needs": "test", "if": "failure()", "runs-on": ["self-hosted"], "steps": steps},
    })
    jobs = await _jobs(db_session, run)
    await _finish(db_session, jobs["build"], "failure")
    # One pass, as after a job completes: the skip of test must cascade.
    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()
    jobs = await _jobs(db_session, run)
    assert jobs["test"].conclusion == "skipped"
    assert jobs["cleanup"].status == "queued"


@pytest.mark.parametrize("expression, expected", [
    ("toJSON(fromJSON('{\"include\":[{\"a\":1},{\"a\":2},{\"b\":3}]}').include.*.a)", "[1,2]"),
    ("toJSON(fromJSON('{\"x\":{\"n\":1},\"y\":{\"n\":2}}').*.n)", "[1,2]"),
    ("toJSON(fromJSON('[[1,2],[3]]')[*][*])", "[1,2,3]"),
    ("fromJSON('{\"include\":[{\"a\":1}]}').include[0].a", "1"),
])
def test_object_filters(expression, expected):
    from app.services.workflow_expressions import render_expressions

    assert render_expressions("${{ " + expression + " }}", {}) == expected


def test_object_filter_semantics_follow_the_runner():
    from app.services.workflow_expressions import render_expressions

    # Present-but-null properties stay; missing ones are dropped.
    assert render_expressions("${{ toJSON(fromJSON('[{\"x\":null},{\"x\":1},{}]').*.x) }}", {}) == "[null,1]"
    # A filtered list stays filtered through parentheses.
    assert render_expressions("${{ toJSON((fromJSON('[{\"x\":1}]').*).x) }}", {}) == "[1]"
    # Hyphen-led dotted segments still tokenize.
    assert render_expressions("${{ github.event.-key }}", {"github": {"event": {"-key": "v"}}}) == "v"


def test_bound_step_conditions_keep_filters():
    """A step condition mixing a server-side filter with steps.* is bound
    for the runner; the filter must be evaluated whole, not as
    ``true.*.name``."""
    from app.services.workflow_expressions import bind_server_context

    ctx = {"github": {"event": {"issue": {"labels": [{"name": "bug"}, {"name": "x"}]}}}}
    bound = bind_server_context(
        "contains(github.event.issue.labels.*.name, 'bug') && steps.check.outputs.ok == 'yes'", ctx,
    )
    assert bound == "contains(fromJSON('[\"bug\",\"x\"]'), 'bug') && steps.check.outputs.ok == 'yes'"
    assert bind_server_context("github.event.issue.labels[0].name == 'bug' && steps.a.outputs.b", ctx) == \
        "'bug' == 'bug' && steps.a.outputs.b"
    # Plain paths bind as before.
    assert bind_server_context("github.event.issue && steps.a.outputs.b", ctx) == "true && steps.a.outputs.b"
