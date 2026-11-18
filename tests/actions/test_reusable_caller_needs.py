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


INNER = {
    "on": {"workflow_call": {}},
    "jobs": {"x": {"runs-on": ["self-hosted"], "steps": [{"run": "echo"}]}},
}

MID = {
    "on": {"workflow_call": {}},
    "jobs": {
        "build": {"uses": "./.github/workflows/inner.yml"},
        "after": {
            "needs": ["build"],
            "runs-on": ["self-hosted"],
            "steps": [{"run": "echo ${{ needs.build.result }}"}],
        },
    },
}


@pytest.mark.asyncio
async def test_a_nested_caller_sharing_a_skipped_top_level_callers_key_is_waited_for(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    """``deploy / after`` needs the ``build`` call inside mid.yml, not the
    run's own skipped ``build`` caller that happens to share its key."""
    async def fake_detect(path, ref="HEAD"):
        return [
            {"_path": ".github/workflows/inner.yml", **INNER},
            {"_path": ".github/workflows/mid.yml", **MID},
        ]

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    workflow_yaml = await workflow_service.materialize_reusable_workflows({
        "name": "caller",
        "jobs": {
            "build": {"if": "false", "uses": "./.github/workflows/inner.yml"},
            "deploy": {"uses": "./.github/workflows/mid.yml"},
        },
    }, "caller.git", "main", None)
    _repo, workflow, _ = executable_workflow
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
    assert sorted(rows) == ["build", "deploy / after", "deploy / build / x"]
    assert rows["build"].conclusion == "skipped"
    assert rows["deploy / after"].needs == ["deploy / build / x"]
    assert rows["deploy / after"].status == "waiting"

    rows["deploy / build / x"].status = "completed"
    rows["deploy / build / x"].conclusion = "success"
    await db_session.commit()
    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()
    after = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run.id, WorkflowJob.job_key == "deploy / after")
    )).scalar_one()
    assert after.status == "queued"
    assert after.steps[0]["run"] == "echo success"


async def _run(monkeypatch, db_session, workflow, test_user, caller, called):
    async def fake_detect(path, ref="HEAD"):
        return [{"_path": f".github/workflows/{name}", **config} for name, config in called.items()]

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    workflow_yaml = await workflow_service.materialize_reusable_workflows(caller, "caller.git", "main", None)
    run = await create_workflow_run(
        db_session, workflow, workflow_yaml, event="push",
        payload={"repository": {"full_name": "testuser/actions-exec-repo"}},
        actor=test_user, head_sha="caller123", head_branch="main",
    )
    await db_session.commit()
    return run


async def _rows(db_session, run):
    return {
        r.job_key: r for r in (await db_session.execute(
            select(WorkflowJob).where(WorkflowJob.run_id == run.id)
        )).scalars().all()
    }


OPTIONAL = {
    "on": {"workflow_call": {}},
    "jobs": {
        "build": {"runs-on": ["self-hosted"], "steps": [{"run": "echo"}]},
        "optional": {"if": "false", "runs-on": ["self-hosted"], "steps": [{"run": "echo"}]},
    },
}

NEEDS_CALL = {
    "name": "caller",
    "jobs": {
        "call": {"uses": "./.github/workflows/called.yml"},
        "after": {
            "needs": ["call"],
            "runs-on": ["self-hosted"],
            "steps": [{"run": "echo ${{ needs.call.result }}"}],
        },
    },
}


@pytest.mark.asyncio
async def test_a_job_skipped_by_its_own_condition_in_the_call_does_not_skip_the_dependent(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    """On GitHub the call succeeds when none of its jobs failed or was
    cancelled, even with one skipped by its own ``if:``, and an ordinary
    ``needs: call`` dependent (implicit ``success()``) runs."""
    _repo, workflow, _ = executable_workflow
    run = await _run(
        monkeypatch, db_session, workflow, test_user, NEEDS_CALL, {"called.yml": OPTIONAL},
    )
    rows = await _rows(db_session, run)
    assert rows["call / optional"].conclusion == "skipped"
    rows["call / build"].status = "completed"
    rows["call / build"].conclusion = "success"
    await db_session.commit()

    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()
    after = (await _rows(db_session, run))["after"]
    assert after.status == "queued"
    assert after.steps[0]["run"] == "echo success"


@pytest.mark.asyncio
async def test_a_failed_job_in_the_call_still_skips_the_dependent(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    called = {
        "on": {"workflow_call": {}},
        "jobs": {
            **OPTIONAL["jobs"],
            "check": {"runs-on": ["self-hosted"], "steps": [{"run": "echo"}]},
        },
    }
    _repo, workflow, _ = executable_workflow
    run = await _run(
        monkeypatch, db_session, workflow, test_user, NEEDS_CALL, {"called.yml": called},
    )
    rows = await _rows(db_session, run)
    rows["call / build"].status = "completed"
    rows["call / build"].conclusion = "success"
    rows["call / check"].status = "completed"
    rows["call / check"].conclusion = "failure"
    await db_session.commit()

    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()
    after = (await _rows(db_session, run))["after"]
    assert after.status == "completed"
    assert after.conclusion == "skipped"


@pytest.mark.asyncio
async def test_a_call_output_read_from_a_nested_callers_output(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    """outer.yml's output reads ``jobs.inner.outputs.v``, where ``inner`` is
    itself a call; inner.yml's output reads its ``leaf`` job's output."""
    called = {
        "outer.yml": {
            "on": {"workflow_call": {"outputs": {"v": {"value": "${{ jobs.inner.outputs.v }}"}}}},
            "jobs": {"inner": {"uses": "./.github/workflows/inner.yml"}},
        },
        "inner.yml": {
            "on": {"workflow_call": {"outputs": {"v": {"value": "${{ jobs.leaf.outputs.v }}"}}}},
            "jobs": {"leaf": {
                "runs-on": ["self-hosted"],
                "outputs": {"v": "${{ steps.s.outputs.v }}"},
                "steps": [{"id": "s", "run": "echo"}],
            }},
        },
    }
    caller = {
        "name": "caller",
        "jobs": {
            "outer": {"uses": "./.github/workflows/outer.yml"},
            "after": {
                "needs": ["outer"],
                "runs-on": ["self-hosted"],
                "steps": [{"run": "echo ${{ needs.outer.outputs.v }} ${{ needs.outer.result }}"}],
            },
        },
    }
    _repo, workflow, _ = executable_workflow
    run = await _run(monkeypatch, db_session, workflow, test_user, caller, called)
    rows = await _rows(db_session, run)
    assert sorted(rows) == ["after", "outer / inner / leaf"]
    rows["outer / inner / leaf"].status = "completed"
    rows["outer / inner / leaf"].conclusion = "success"
    rows["outer / inner / leaf"].outputs = {"v": "deep"}
    await db_session.commit()

    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()
    after = (await _rows(db_session, run))["after"]
    assert after.status == "queued"
    assert after.steps[0]["run"] == "echo deep success"


def test_a_nested_callers_entry_is_built_from_its_own_alias():
    aliases = {"outer": {
        "jobs": {"inner / leaf": "outer / inner / leaf", "skipped": "outer / skipped"},
        "outputs": {"v": "${{ jobs.inner.outputs.v }}/${{ jobs.inner.result }}"},
        "callers": {"inner": {
            "jobs": {"leaf": "outer / inner / leaf"},
            "outputs": {"v": "${{ jobs.leaf.outputs.v }}"},
        }},
    }}
    needs = {
        "outer / inner / leaf": {"result": "success", "outputs": {"v": "deep"}},
        "outer / skipped": {"result": "skipped", "outputs": {}},
    }
    entry = needs_alias_entries(aliases, needs)["outer"]
    assert entry == {"result": "success", "outputs": {"v": "deep/success"}}


VERSIONED = {
    "on": {"workflow_call": {
        "inputs": {
            "version": {"type": "string", "required": False},
            "channel": {"type": "string", "default": "stable"},
        },
        "outputs": {
            "version": {"value": "${{ inputs.version }}"},
            "channel": {"value": "${{ inputs.channel }}"},
            "event": {"value": "${{ inputs.version }}-${{ github.event_name }}"},
        },
    }},
    "jobs": {"build": {"runs-on": ["self-hosted"], "steps": [{"run": "echo"}]}},
}


async def _promote_after(db_session, run, done_keys, after_key="after"):
    rows = await _rows(db_session, run)
    for key in done_keys:
        rows[key].status = "completed"
        rows[key].conclusion = "success"
    await db_session.commit()
    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()
    return (await _rows(db_session, run))[after_key]


@pytest.mark.asyncio
async def test_a_call_output_reads_the_inputs_the_caller_passed(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    """``on.workflow_call.outputs.<id>.value`` may read ``inputs`` and
    ``github``; with only ``jobs`` there, ``inputs.version`` came out empty."""
    caller = {
        "name": "caller",
        "jobs": {
            "call": {"uses": "./.github/workflows/called.yml", "with": {"version": "1.2"}},
            "after": {
                "needs": ["call"],
                "runs-on": ["self-hosted"],
                "steps": [{"run": "echo ${{ needs.call.outputs.version }} ${{ needs.call.outputs.event }}"}],
            },
        },
    }
    _repo, workflow, _ = executable_workflow
    run = await _run(monkeypatch, db_session, workflow, test_user, caller, {"called.yml": VERSIONED})
    after = await _promote_after(db_session, run, ["call / build"])
    assert after.status == "queued"
    assert after.steps[0]["run"] == "echo 1.2 1.2-push"


@pytest.mark.asyncio
async def test_a_call_output_reads_a_defaulted_input_the_caller_omitted(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    caller = {
        "name": "caller",
        "jobs": {
            "call": {"uses": "./.github/workflows/called.yml", "with": {"version": "1.2"}},
            "after": {
                "needs": ["call"],
                "runs-on": ["self-hosted"],
                "steps": [{"run": "echo ${{ needs.call.outputs.channel }}"}],
            },
        },
    }
    _repo, workflow, _ = executable_workflow
    run = await _run(monkeypatch, db_session, workflow, test_user, caller, {"called.yml": VERSIONED})
    after = await _promote_after(db_session, run, ["call / build"])
    assert after.status == "queued"
    assert after.steps[0]["run"] == "echo stable"


@pytest.mark.asyncio
async def test_a_nested_calls_output_from_its_inputs_is_forwarded_by_the_outer_call(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    """inner.yml's output reads its own ``inputs.tag``, passed by outer.yml
    from outer's ``inputs.release``; outer.yml forwards it as its output."""
    called = {
        "outer.yml": {
            "on": {"workflow_call": {
                "inputs": {"release": {"type": "string"}},
                "outputs": {"tag": {"value": "${{ jobs.inner.outputs.tag }}"}},
            }},
            "jobs": {"inner": {
                "uses": "./.github/workflows/inner.yml",
                "with": {"tag": "v${{ inputs.release }}"},
            }},
        },
        "inner.yml": {
            "on": {"workflow_call": {
                "inputs": {"tag": {"type": "string"}},
                "outputs": {"tag": {"value": "${{ inputs.tag }}"}},
            }},
            "jobs": {"leaf": {"runs-on": ["self-hosted"], "steps": [{"run": "echo"}]}},
        },
    }
    caller = {
        "name": "caller",
        "jobs": {
            "outer": {"uses": "./.github/workflows/outer.yml", "with": {"release": "3.4"}},
            "after": {
                "needs": ["outer"],
                "runs-on": ["self-hosted"],
                "steps": [{"run": "echo ${{ needs.outer.outputs.tag }}"}],
            },
        },
    }
    _repo, workflow, _ = executable_workflow
    run = await _run(monkeypatch, db_session, workflow, test_user, caller, called)
    after = await _promote_after(db_session, run, ["outer / inner / leaf"])
    assert after.status == "queued"
    assert after.steps[0]["run"] == "echo v3.4"


def test_a_call_output_reads_the_runs_github_and_vars_contexts():
    aliases = {"call": {
        "jobs": {"a": "call / a"},
        "inputs": {"ref": "${{ github.ref_name }}"},
        "outputs": {"v": "${{ inputs.ref }}/${{ github.event_name }}/${{ vars.REGION }}"},
    }}
    needs = {"call / a": {"result": "success", "outputs": {}}}
    run_context = {
        "github": {"ref_name": "main", "event_name": "push"},
        "vars": {"REGION": "eu"},
        "secrets": {"TOKEN": "not-exposed"},
    }
    entry = needs_alias_entries(aliases, needs, run_context)["call"]
    assert entry["outputs"] == {"v": "main/push/eu"}


def test_a_call_output_cannot_read_secrets():
    aliases = {"call": {"jobs": {}, "outputs": {"v": "${{ secrets.TOKEN }}"}}}
    entry = needs_alias_entries(aliases, {}, {"secrets": {"TOKEN": "hidden"}})["call"]
    assert entry["outputs"] == {"v": ""}
