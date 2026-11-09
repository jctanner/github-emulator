"""Step ``outcome`` and ``conclusion``, and ``continue-on-error``.

Fullsend's reusable dispatch guards its CEL route on an earlier step:
``if: steps.cel-config.outcome == 'success'``. The runner exposed only
``steps.<id>.outputs``, so the condition was always false, the CEL route never
ran, and every agent trigger in ``.fullsend`` config was ignored. Steps that
set ``continue-on-error`` failed the job as though the setting were absent.
"""

import importlib.util
from pathlib import Path

from app.services.workflow_service import build_steps_data


RUNNER_PATH = (
    Path(__file__).parents[2] / "src" / "runners" / "emulator" / "runner.py"
)
_SPEC = importlib.util.spec_from_file_location("github_emulator_runner_outcome", RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner_module)


def _run_job(monkeypatch, tmp_path, steps):
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path / "work"))
    monkeypatch.setattr(runner_module, "RUNNER_TEMP", str(tmp_path / "temp"))
    client = runner_module.RunnerClient.__new__(runner_module.RunnerClient)
    client._masks = set()
    logs: list[str] = []
    completed: list[tuple] = []
    client._upload_logs = lambda repository, job_id, chunk: logs.append(chunk)
    client._report_progress = lambda repository, job_id, steps: None
    client._complete_job = (
        lambda repository, job_id, conclusion, steps, step_outputs=None:
        completed.append((conclusion, steps))
    )
    numbered = [{"number": i + 1, **step} for i, step in enumerate(steps)]
    client.execute_job({"job_id": 1, "repository": "o/r", "steps": numbered})
    conclusion, reported = completed[0]
    return conclusion, {s["name"]: s["conclusion"] for s in reported}, "".join(logs)


def test_a_later_step_reads_an_earlier_steps_outcome(monkeypatch, tmp_path):
    conclusion, steps, _ = _run_job(monkeypatch, tmp_path, [
        {"name": "config", "id": "cel-config", "run": "true"},
        {"name": "guarded", "if": "steps.cel-config.outcome == 'success'", "run": "true"},
        {"name": "by conclusion", "if": "steps.cel-config.conclusion == 'success'", "run": "true"},
    ])
    assert conclusion == "success"
    assert steps == {"config": "success", "guarded": "success", "by conclusion": "success"}


def test_continue_on_error_keeps_the_job_and_reports_both_results(monkeypatch, tmp_path):
    conclusion, steps, log = _run_job(monkeypatch, tmp_path, [
        {"name": "flaky", "id": "flaky", "run": "exit 3", "continue-on-error": True},
        {"name": "on outcome", "if": "steps.flaky.outcome == 'failure'", "run": "true"},
        {"name": "on conclusion", "if": "steps.flaky.conclusion == 'success'", "run": "true"},
        {"name": "plain", "run": "true"},
    ])
    assert conclusion == "success"
    assert steps == {
        "flaky": "success", "on outcome": "success",
        "on conclusion": "success", "plain": "success",
    }
    assert "continue-on-error" in log


def test_continue_on_error_rendered_false_still_fails(monkeypatch, tmp_path):
    conclusion, steps, _ = _run_job(monkeypatch, tmp_path, [
        {"name": "broken", "id": "broken", "run": "exit 1", "continue-on-error": "false"},
        {"name": "plain", "run": "true"},
    ])
    assert conclusion == "failure"
    assert steps == {"broken": "failure", "plain": "skipped"}


def test_a_skipped_step_has_outcome_skipped(monkeypatch, tmp_path):
    conclusion, steps, _ = _run_job(monkeypatch, tmp_path, [
        {"name": "off", "id": "off", "if": "false", "run": "true"},
        {"name": "after", "if": "steps.off.outcome == 'skipped'", "run": "true"},
        {"name": "not success", "if": "steps.off.outcome == 'success'", "run": "true"},
    ])
    assert conclusion == "success"
    assert steps == {"off": "skipped", "after": "success", "not success": "skipped"}


def test_the_server_passes_continue_on_error_rendered():
    steps = build_steps_data(
        [
            {"run": "x", "continue-on-error": True},
            {"run": "y", "continue-on-error": "${{ inputs.lenient }}"},
            {"run": "z"},
        ],
        {},
        {"inputs": {"lenient": False}},
    )
    assert steps[0]["continue-on-error"] is True
    assert steps[1]["continue-on-error"] in ("false", False)
    assert "continue-on-error" not in steps[2]


def test_a_steps_dependent_continue_on_error_is_left_for_the_runner(monkeypatch, tmp_path):
    raw = "${{ steps.mode.outputs.lenient == 'yes' }}"
    [data] = build_steps_data([{"run": "x", "continue-on-error": raw}], {}, {})
    assert data["continue-on-error"] == raw
    conclusion, steps, _ = _run_job(monkeypatch, tmp_path, [
        {"name": "mode", "id": "mode", "run": 'echo "lenient=yes" >> "$GITHUB_OUTPUT"'},
        {"name": "flaky", "run": "exit 1", "continue-on-error": raw},
    ])
    assert conclusion == "success"
    assert steps == {"mode": "success", "flaky": "success"}


def test_continue_on_error_reads_the_steps_own_outputs(monkeypatch, tmp_path):
    conclusion, steps, _ = _run_job(monkeypatch, tmp_path, [
        {"name": "probe", "id": "probe",
         "run": 'echo "lenient=yes" >> "$GITHUB_OUTPUT"; exit 1',
         "continue-on-error": "${{ steps.probe.outputs.lenient == 'yes' }}"},
        {"name": "plain", "run": "true"},
    ])
    assert conclusion == "success"
    assert steps == {"probe": "success", "plain": "success"}


def test_a_mixed_continue_on_error_has_its_server_contexts_bound(monkeypatch, tmp_path):
    raw = "${{ inputs.lenient && steps.mode.outputs.lenient == 'yes' }}"
    [data] = build_steps_data(
        [{"run": "x", "continue-on-error": raw}], {}, {"inputs": {"lenient": True}},
    )
    assert "inputs." not in data["continue-on-error"]
    assert "steps.mode.outputs.lenient" in data["continue-on-error"]
    conclusion, steps, _ = _run_job(monkeypatch, tmp_path, [
        {"name": "mode", "id": "mode", "run": 'echo "lenient=yes" >> "$GITHUB_OUTPUT"'},
        {"name": "flaky", "run": "exit 1", "continue-on-error": data["continue-on-error"]},
    ])
    assert conclusion == "success"
    assert steps == {"mode": "success", "flaky": "success"}
