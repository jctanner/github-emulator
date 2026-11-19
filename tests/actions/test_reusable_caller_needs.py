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


@pytest.mark.asyncio
async def test_a_top_level_calls_compound_with_reads_the_dispatch_inputs(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    """A compound ``with:`` expression on the run's own inputs is rendered at
    promotion; ``needs.<caller>.outputs`` must agree with what the called job
    received, not render ``inputs`` as empty."""
    called = {"called.yml": {
        "on": {"workflow_call": {
            "inputs": {"v": {"type": "string"}},
            "outputs": {"v": {"value": "${{ inputs.v }}"}},
        }},
        "jobs": {"build": {"runs-on": ["self-hosted"], "steps": [{"run": "echo ${{ inputs.v }}"}]}},
    }}
    caller = {
        "name": "caller",
        "jobs": {
            "call": {
                "uses": "./.github/workflows/called.yml",
                "with": {"v": "${{ format('{0}-x', inputs.ver) }}"},
            },
            "after": {
                "needs": ["call"],
                "runs-on": ["self-hosted"],
                "steps": [{"run": "echo ${{ needs.call.outputs.v }}"}],
            },
        },
    }

    async def fake_detect(path, ref="HEAD"):
        return [{"_path": f".github/workflows/{name}", **config} for name, config in called.items()]

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    inputs = {"ver": "1.0"}
    workflow_yaml = await workflow_service.materialize_reusable_workflows(
        caller, "caller.git", "main", None, inputs=inputs,
    )
    _repo, workflow, _ = executable_workflow
    run = await create_workflow_run(
        db_session, workflow, workflow_yaml, event="workflow_dispatch",
        payload={"repository": {"full_name": "testuser/actions-exec-repo"}, "inputs": inputs},
        actor=test_user, head_sha="caller123", head_branch="main",
    )
    await db_session.commit()
    assert (await _rows(db_session, run))["call / build"].steps[0]["run"] == "echo 1.0-x"
    after = await _promote_after(db_session, run, ["call / build"])
    assert after.status == "queued"
    assert after.steps[0]["run"] == "echo 1.0-x"


CHAIN = {
    "a.yml": {
        "on": {"workflow_call": {"outputs": {"o": {"value": "${{ jobs.build.outputs.o }}"}}}},
        "jobs": {"build": {
            "runs-on": ["self-hosted"],
            "outputs": {"o": "${{ steps.s.outputs.o }}"},
            "steps": [{"id": "s", "run": "echo"}],
        }},
    },
    "b.yml": {
        "on": {"workflow_call": {
            "inputs": {"x": {"type": "string"}},
            "outputs": {"out": {"value": "${{ inputs.x }}"}},
        }},
        "jobs": {"use": {"runs-on": ["self-hosted"], "steps": [{"run": "echo b ${{ inputs.x }}"}]}},
    },
    "c.yml": {
        "on": {"workflow_call": {
            "inputs": {"y": {"type": "string"}},
            "outputs": {"z": {"value": "${{ inputs.y }}"}},
        }},
        "jobs": {"use": {"runs-on": ["self-hosted"], "steps": [{"run": "echo c ${{ inputs.y }}"}]}},
    },
}


@pytest.mark.asyncio
async def test_an_output_forwarded_through_a_chain_of_calls(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    """A -> B -> C: B passes ``needs.A.outputs.o`` as its input and outputs
    it; C reads it from ``needs.B.outputs``. Rendering B's ``with:`` for C
    needs ``needs.A``, the entry built from A's called jobs."""
    caller = {
        "name": "caller",
        "jobs": {
            # C first: the order of the jobs must not matter.
            "C": {
                "uses": "./.github/workflows/c.yml",
                "needs": "B",
                "with": {"y": "${{ needs.B.outputs.out }}"},
            },
            "B": {
                "uses": "./.github/workflows/b.yml",
                "needs": ["A"],
                "with": {"x": "${{ needs.A.outputs.o }}"},
            },
            "A": {"uses": "./.github/workflows/a.yml"},
            "after": {
                "needs": ["C"],
                "runs-on": ["self-hosted"],
                "steps": [{"run": "echo ${{ needs.C.outputs.z }} ${{ needs.C.result }}"}],
            },
        },
    }
    _repo, workflow, _ = executable_workflow
    run = await _run(monkeypatch, db_session, workflow, test_user, caller, CHAIN)

    async def finish(key, outputs=None):
        row = (await _rows(db_session, run))[key]
        row.status = "completed"
        row.conclusion = "success"
        row.outputs = outputs or {}
        await db_session.commit()
        await dispatch_ready_jobs(db_session, run.id)
        await db_session.commit()

    await finish("A / build", {"o": "forty"})
    rows = await _rows(db_session, run)
    assert rows["B / use"].status == "queued"
    assert rows["B / use"].steps[0]["run"] == "echo b forty"
    await finish("B / use")
    rows = await _rows(db_session, run)
    assert rows["C / use"].status == "queued"
    assert rows["C / use"].steps[0]["run"] == "echo c forty"
    await finish("C / use")
    after = (await _rows(db_session, run))["after"]
    assert after.status == "queued"
    assert after.steps[0]["run"] == "echo forty success"


def test_a_chained_calls_with_is_rendered_with_its_own_needs_on_callers():
    alias_a = {"jobs": {"build": "A / build"}, "outputs": {"o": "${{ jobs.build.outputs.o }}"}}
    aliases = {"B": {
        "jobs": {"use": "B / use"},
        "inputs": {"x": "${{ needs.A.outputs.o }}"},
        "outputs": {"out": "${{ inputs.x }}"},
        "needs_aliases": {"A": alias_a},
    }}
    needs = {
        "A / build": {"result": "success", "outputs": {"o": "forty"}},
        "B / use": {"result": "success", "outputs": {}},
    }
    assert needs_alias_entries(aliases, needs)["B"]["outputs"] == {"out": "forty"}


@pytest.mark.asyncio
async def test_a_nested_calls_compound_with_reads_its_own_workflows_inputs(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    """Inside outer.yml, ``needs.inner.outputs`` renders inner's ``with:``
    against outer.yml's inputs, not the run's (``ver`` is in both)."""
    called = {
        "outer.yml": {
            "on": {"workflow_call": {"inputs": {"ver": {"type": "string"}}}},
            "jobs": {
                "inner": {
                    "uses": "./.github/workflows/inner.yml",
                    "with": {"tag": "${{ format('v{0}', inputs.ver) }}"},
                },
                "report": {
                    "needs": "inner",
                    "runs-on": ["self-hosted"],
                    "steps": [{"run": "echo ${{ needs.inner.outputs.tag }}"}],
                },
            },
        },
        "inner.yml": {
            "on": {"workflow_call": {
                "inputs": {"tag": {"type": "string"}},
                "outputs": {"tag": {"value": "${{ inputs.tag }}"}},
            }},
            "jobs": {"leaf": {"runs-on": ["self-hosted"], "steps": [{"run": "echo ${{ inputs.tag }}"}]}},
        },
    }
    caller = {"name": "caller", "jobs": {
        "outer": {"uses": "./.github/workflows/outer.yml", "with": {"ver": "2"}},
    }}

    async def fake_detect(path, ref="HEAD"):
        return [{"_path": f".github/workflows/{name}", **config} for name, config in called.items()]

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    inputs = {"ver": "run"}
    workflow_yaml = await workflow_service.materialize_reusable_workflows(
        caller, "caller.git", "main", None, inputs=inputs,
    )
    _repo, workflow, _ = executable_workflow
    run = await create_workflow_run(
        db_session, workflow, workflow_yaml, event="workflow_dispatch",
        payload={"repository": {"full_name": "testuser/actions-exec-repo"}, "inputs": inputs},
        actor=test_user, head_sha="caller123", head_branch="main",
    )
    await db_session.commit()
    assert (await _rows(db_session, run))["outer / inner / leaf"].steps[0]["run"] == "echo v2"
    report = await _promote_after(db_session, run, ["outer / inner / leaf"], "outer / report")
    assert report.status == "queued"
    assert report.steps[0]["run"] == "echo v2"


THREE_LEVELS = {
    "outer.yml": {
        "on": {"workflow_call": {
            "inputs": {"ver": {"type": "string"}},
            "outputs": {
                "tag": {"value": "${{ jobs.mid.outputs.tag }}"},
                "plain": {"value": "${{ jobs.mid.outputs.plain }}"},
            },
        }},
        "jobs": {"mid": {
            "uses": "./.github/workflows/mid.yml",
            "with": {"ver": "${{ format('{0}-mid', inputs.ver) }}"},
        }},
    },
    "mid.yml": {
        "on": {"workflow_call": {
            "inputs": {"ver": {"type": "string"}},
            "outputs": {
                "tag": {"value": "${{ jobs.inner.outputs.tag }}"},
                "plain": {"value": "${{ jobs.inner.outputs.plain }}"},
            },
        }},
        "jobs": {
            "inner": {
                "uses": "./.github/workflows/inner.yml",
                "with": {"tag": "${{ format('v{0}', inputs.ver) }}", "plain": "${{ inputs.ver }}"},
            },
            "show": {"runs-on": ["self-hosted"], "steps": [{"run": "echo ${{ inputs.ver }}"}]},
            "report": {
                "needs": "inner",
                "runs-on": ["self-hosted"],
                "steps": [{"run": "echo ${{ needs.inner.outputs.tag }} ${{ needs.inner.outputs.plain }} ${{ inputs.ver }}"}],
            },
        },
    },
    "inner.yml": {
        "on": {"workflow_call": {
            "inputs": {"tag": {"type": "string"}, "plain": {"type": "string"}},
            "outputs": {
                "tag": {"value": "${{ inputs.tag }}"},
                "plain": {"value": "${{ inputs.plain }}"},
            },
        }},
        "jobs": {"leaf": {
            "runs-on": ["self-hosted"],
            "steps": [{"run": "echo ${{ inputs.tag }} ${{ inputs.plain }}"}],
        }},
    },
}


async def _three_level_run(monkeypatch, executable_workflow, db_session, test_user):  # noqa: F811
    caller = {"name": "caller", "jobs": {
        "outer": {"uses": "./.github/workflows/outer.yml", "with": {"ver": "2"}},
        "after": {
            "needs": "outer",
            "runs-on": ["self-hosted"],
            "steps": [{"run": "echo ${{ needs.outer.outputs.tag }} ${{ needs.outer.outputs.plain }}"}],
        },
    }}

    async def fake_detect(path, ref="HEAD"):
        return [{"_path": f".github/workflows/{name}", **config} for name, config in THREE_LEVELS.items()]

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    inputs = {"ver": "run"}
    workflow_yaml = await workflow_service.materialize_reusable_workflows(
        caller, "caller.git", "main", None, inputs=inputs,
    )
    _repo, workflow, _ = executable_workflow
    run = await create_workflow_run(
        db_session, workflow, workflow_yaml, event="workflow_dispatch",
        payload={"repository": {"full_name": "testuser/actions-exec-repo"}, "inputs": inputs},
        actor=test_user, head_sha="caller123", head_branch="main",
    )
    await db_session.commit()
    return run


@pytest.mark.asyncio
async def test_a_twice_nested_calls_compound_with_reads_each_levels_own_inputs(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    """run (ver=run) -> outer (ver=2) -> mid (ver=format('{0}-mid', inputs.ver))
    -> inner (tag=format('v{0}', inputs.ver), plain=inputs.ver). Each
    ``with:`` reads the inputs of the workflow it is written in: mid's ver is
    '2-mid', inner's tag 'v2-mid' and plain '2-mid', both for the called jobs
    and for needs.inner in mid."""
    run = await _three_level_run(monkeypatch, executable_workflow, db_session, test_user)
    rows = await _rows(db_session, run)
    assert rows["outer / mid / show"].steps[0]["run"] == "echo 2-mid"
    assert rows["outer / mid / inner / leaf"].steps[0]["run"] == "echo v2-mid 2-mid"
    report = await _promote_after(
        db_session, run, ["outer / mid / inner / leaf"], "outer / mid / report",
    )
    assert report.status == "queued"
    assert report.steps[0]["run"] == "echo v2-mid 2-mid 2-mid"


@pytest.mark.asyncio
async def test_an_output_forwarded_up_from_a_twice_nested_call(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    """inner's output, forwarded by mid and by outer, reaches the run's own
    dependent with the value inner received."""
    run = await _three_level_run(monkeypatch, executable_workflow, db_session, test_user)
    after = await _promote_after(
        db_session, run,
        ["outer / mid / inner / leaf", "outer / mid / show", "outer / mid / report"],
    )
    assert after.status == "queued"
    assert after.steps[0]["run"] == "echo v2-mid 2-mid"


def _chain(final_if):
    return {
        "name": "caller",
        "jobs": {
            "call": {"uses": "./.github/workflows/called.yml"},
            "middle": {"needs": ["call"], "runs-on": ["self-hosted"], "steps": [{"run": "echo"}]},
            "final": {
                "needs": ["middle"],
                "if": final_if,
                "runs-on": ["self-hosted"],
                "steps": [{"run": "echo"}],
            },
        },
    }


async def _finish(db_session, run, conclusions):
    rows = await _rows(db_session, run)
    for key, conclusion in conclusions.items():
        rows[key].status = "completed"
        rows[key].conclusion = conclusion
    await db_session.commit()
    await dispatch_ready_jobs(db_session, run.id)
    await db_session.commit()
    return await _rows(db_session, run)


@pytest.mark.asyncio
async def test_success_further_down_reads_the_calls_result_not_its_skipped_job(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    """call -> middle -> final (``if: success()``). ``success()`` answers for
    every ancestor, and the ancestor here is the call, which succeeded: its
    job skipped by its own ``if:`` does not count against it."""
    _repo, workflow, _ = executable_workflow
    run = await _run(
        monkeypatch, db_session, workflow, test_user, _chain("success()"), {"called.yml": OPTIONAL},
    )
    rows = await _finish(db_session, run, {"call / build": "success"})
    assert rows["middle"].status == "queued"
    rows = await _finish(db_session, run, {"middle": "success"})
    assert rows["final"].status == "queued"


@pytest.mark.asyncio
async def test_success_further_down_inside_a_called_workflow_reads_the_nested_calls_result(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
):
    """The same chain one level down, inside ``deploy``: ``deploy / final``'s
    ancestor is the ``deploy / call`` call, not its skipped job."""
    chain = _chain("success()")
    mid = {"on": {"workflow_call": {}}, "jobs": chain["jobs"]}
    caller = {"name": "caller", "jobs": {"deploy": {"uses": "./.github/workflows/mid.yml"}}}
    _repo, workflow, _ = executable_workflow
    run = await _run(
        monkeypatch, db_session, workflow, test_user, caller,
        {"called.yml": OPTIONAL, "mid.yml": mid},
    )
    rows = await _finish(db_session, run, {"deploy / call / build": "success"})
    assert rows["deploy / call / optional"].conclusion == "skipped"
    assert rows["deploy / middle"].status == "queued"
    rows = await _finish(db_session, run, {"deploy / middle": "success"})
    assert rows["deploy / final"].status == "queued"


FAILING = {
    "on": {"workflow_call": {}},
    "jobs": {
        **OPTIONAL["jobs"],
        "check": {"runs-on": ["self-hosted"], "steps": [{"run": "echo"}]},
    },
}


@pytest.mark.asyncio
@pytest.mark.parametrize(("final_if", "final_status", "final_conclusion"), [
    ("success()", "completed", "skipped"),
    ("failure()", "queued", None),
])
async def test_a_failed_job_in_the_call_is_seen_further_down(
    monkeypatch, executable_workflow, db_session, test_user,  # noqa: F811
    final_if, final_status, final_conclusion,
):
    """A failed called job fails the call: middle (no ``if:``) is skipped, and
    final sees the failed ancestor, as it would behind a plain failed job."""
    _repo, workflow, _ = executable_workflow
    run = await _run(
        monkeypatch, db_session, workflow, test_user, _chain(final_if), {"called.yml": FAILING},
    )
    rows = await _finish(db_session, run, {"call / build": "success", "call / check": "failure"})
    assert rows["middle"].conclusion == "skipped"
    assert rows["final"].status == final_status
    assert rows["final"].conclusion == final_conclusion
