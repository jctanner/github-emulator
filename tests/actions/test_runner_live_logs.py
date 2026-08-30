"""Tests for incremental output from the bundled Actions runner."""

import importlib.util
import time
from pathlib import Path


RUNNER_PATH = (
    Path(__file__).parents[2]
    / "src"
    / "runners"
    / "emulator"
    / "runner.py"
)
_SPEC = importlib.util.spec_from_file_location("github_emulator_runner", RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner_module)


def test_step_if_can_read_previous_step_outputs():
    outputs = {"route": {"stage": ""}}
    assert not runner_module._evaluate_step_if(
        "steps.route.outputs.stage != ''", outputs,
    )
    outputs["route"]["stage"] = "triage"
    assert runner_module._evaluate_step_if(
        "steps.route.outputs.stage != ''", outputs,
    )


def test_step_if_supports_actions_boolean_subset():
    outputs = {"route": {"stage": "review"}}
    assert runner_module._evaluate_step_if(
        "steps.route.outputs.stage == 'review' && always()", outputs,
    )
    assert not runner_module._evaluate_step_if(
        "steps.route.outputs.stage == 'code' || false", outputs,
    )


def test_runner_uploads_output_before_job_completion(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_module, "_start_oidc_broker", lambda: None)
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))

    runner = runner_module.RunnerClient()
    uploads = []
    completions = []
    runner._upload_logs = lambda repository, job_id, chunk: uploads.append(
        (time.monotonic(), chunk)
    )
    runner._report_progress = lambda repository, job_id, steps: None
    runner._complete_job = (
        lambda repository, job_id, conclusion, steps: completions.append(
            time.monotonic()
        )
    )

    runner.execute_job({
        "job_id": 7,
        "name": "streaming",
        "steps": [{
            "number": 1,
            "name": "Long step",
            "run": "printf 'first line\\n'; sleep 0.2; printf 'second line\\n'",
            "shell": "bash",
        }],
        "timeout_seconds": 10,
    })

    assert completions
    assert uploads
    completion_time = completions[0]
    first_output = next(timestamp for timestamp, chunk in uploads if "first line" in chunk)
    assert first_output < completion_time
    combined = "".join(chunk for _timestamp, chunk in uploads)
    assert "first line\n" in combined
    assert "second line\n" in combined
