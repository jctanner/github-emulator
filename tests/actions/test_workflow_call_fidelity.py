"""Reusable-workflow and concurrency behaviour that differed from GitHub.

All three were latent rather than loud: the dispatch's guards worked by
coincidence, `secrets: inherit` looked honoured while passing nothing, and a
concurrency group cancelled runs nobody asked to cancel.
"""

from types import SimpleNamespace

import pytest

from app.services import workflow_service
from app.services.workflow_service import (
    _job_concurrency,
    apply_workflow_call_inputs,
    build_job_graph,
)


CALLED = {
    "on": {
        "workflow_call": {
            "inputs": {
                "install_mode": {"type": "string", "default": "per-repo"},
                "verbose": {"type": "boolean", "default": False},
                "retries": {"type": "number", "default": 3},
                "agent": {"type": "string", "required": True},
                "optional_note": {"type": "string"},
            }
        }
    },
    "jobs": {},
}


def test_declared_defaults_are_applied_when_a_caller_omits_an_input():
    """An unsupplied input rendered empty, so `inputs.x == 'per-repo'` was
    comparing against "" and only worked when the caller passed it."""
    resolved, missing = apply_workflow_call_inputs(CALLED, {"agent": "triage"})
    assert resolved["install_mode"] == "per-repo"
    assert resolved["retries"] == 3
    assert missing == []


def test_a_supplied_value_wins_over_the_default():
    resolved, _ = apply_workflow_call_inputs(
        CALLED, {"agent": "triage", "install_mode": "per-org"}
    )
    assert resolved["install_mode"] == "per-org"


def test_boolean_inputs_are_coerced():
    """Values arrive from YAML or rendered expressions, so a boolean input
    often arrives as the string "true"; comparing that to a boolean silently
    takes the wrong branch."""
    resolved, _ = apply_workflow_call_inputs(CALLED, {"agent": "a", "verbose": "true"})
    assert resolved["verbose"] is True
    resolved, _ = apply_workflow_call_inputs(CALLED, {"agent": "a", "verbose": "false"})
    assert resolved["verbose"] is False
    # The default is a real boolean and must stay one.
    resolved, _ = apply_workflow_call_inputs(CALLED, {"agent": "a"})
    assert resolved["verbose"] is False


def test_number_inputs_are_coerced():
    resolved, _ = apply_workflow_call_inputs(CALLED, {"agent": "a", "retries": "7"})
    assert resolved["retries"] == 7


def test_a_missing_required_input_is_reported():
    _, missing = apply_workflow_call_inputs(CALLED, {})
    assert missing == ["agent"]


def test_an_input_with_no_default_and_not_required_is_left_alone():
    resolved, _ = apply_workflow_call_inputs(CALLED, {"agent": "a"})
    assert "optional_note" not in resolved


def test_job_concurrency_defaults_to_not_cancelling():
    """GitHub defaults cancel-in-progress to false: a group serialises runs
    unless the workflow asks for supersede."""
    group, cancel = _job_concurrency({"concurrency": {"group": "g"}}, {})
    assert group == "g"
    assert cancel is False


def test_job_concurrency_honours_an_explicit_cancel():
    group, cancel = _job_concurrency(
        {"concurrency": {"group": "g", "cancel-in-progress": True}}, {}
    )
    assert (group, cancel) == ("g", True)


def test_a_bare_string_is_a_group_name():
    group, cancel = _job_concurrency({"concurrency": "plain"}, {})
    assert (group, cancel) == ("plain", False)


def test_no_concurrency_means_no_group():
    assert _job_concurrency({}, {}) == (None, False)


def test_job_level_concurrency_survives_the_job_graph():
    """It has to reach job creation to do anything; the graph dropped it."""
    graph = build_job_graph(
        {
            "jobs": {
                "triage": {
                    "runs-on": "ubuntu-latest",
                    "concurrency": {"group": "role-triage", "cancel-in-progress": True},
                    "steps": [{"run": "true"}],
                }
            }
        }
    )
    assert graph[0]["concurrency"] == {
        "group": "role-triage",
        "cancel-in-progress": True,
    }


# --- through the real materialiser -------------------------------------


def _fake_db_and_detect(monkeypatch, called_workflow):
    """Stand up the minimum a reusable-workflow call needs to resolve."""
    config_repo = SimpleNamespace(full_name="org/config", disk_path="config.git")

    async def fake_detect(path, ref="HEAD"):
        return [{"_path": ".github/workflows/called.yml", **called_workflow}]

    class Result:
        def scalar_one_or_none(self):
            return config_repo

    class FakeDB:
        async def execute(self, _query):
            return Result()

    monkeypatch.setattr(workflow_service, "detect_workflows", fake_detect)
    return FakeDB()


@pytest.mark.asyncio
async def test_secrets_inherit_passes_the_callers_secrets(monkeypatch):
    """`secrets: inherit` is a string, not a mapping. Parsing it as one and
    discarding it handed the called workflow nothing while looking honoured."""
    db = _fake_db_and_detect(monkeypatch, {
        "jobs": {
            "agent": {
                "runs-on": ["self-hosted"],
                "env": {"TOKEN": "${{ secrets.MY_SECRET }}"},
                "steps": [{"run": "true"}],
            }
        }
    })
    result = await workflow_service.materialize_reusable_workflows(
        {
            "jobs": {
                "stage": {
                    "uses": "org/config/.github/workflows/called.yml@main",
                    "secrets": "inherit",
                }
            }
        },
        "caller.git",
        "main",
        db,
        secrets={"MY_SECRET": "sealed-value"},
    )
    job = result["jobs"]["stage / agent"]
    assert job["env"]["TOKEN"] == "sealed-value"


@pytest.mark.asyncio
async def test_explicit_secrets_still_win_over_inherit_shape(monkeypatch):
    db = _fake_db_and_detect(monkeypatch, {
        "jobs": {
            "agent": {
                "runs-on": ["self-hosted"],
                "env": {"TOKEN": "${{ secrets.MY_SECRET }}"},
                "steps": [{"run": "true"}],
            }
        }
    })
    result = await workflow_service.materialize_reusable_workflows(
        {
            "jobs": {
                "stage": {
                    "uses": "org/config/.github/workflows/called.yml@main",
                    "secrets": {"MY_SECRET": "explicit"},
                }
            }
        },
        "caller.git",
        "main",
        db,
        secrets={"MY_SECRET": "inherited"},
    )
    assert result["jobs"]["stage / agent"]["env"]["TOKEN"] == "explicit"


@pytest.mark.asyncio
async def test_a_called_workflows_default_reaches_its_jobs(monkeypatch):
    """The end the guards actually read: a default must survive into the
    inlined job, not just into the resolved input dict."""
    db = _fake_db_and_detect(monkeypatch, {
        "on": {"workflow_call": {"inputs": {"mode": {"type": "string", "default": "per-repo"}}}},
        "jobs": {
            "agent": {
                "runs-on": ["self-hosted"],
                "env": {"MODE": "${{ inputs.mode }}"},
                "steps": [{"run": "true"}],
            }
        },
    })
    result = await workflow_service.materialize_reusable_workflows(
        {"jobs": {"stage": {"uses": "org/config/.github/workflows/called.yml@main"}}},
        "caller.git",
        "main",
        db,
    )
    assert result["jobs"]["stage / agent"]["env"]["MODE"] == "per-repo"
