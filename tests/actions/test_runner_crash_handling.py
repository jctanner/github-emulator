"""A crashing job must end, and hashFiles must not be what crashes it.

A run sat `in_progress` for ever: no conclusion, no log line, nothing in the
run to look at. The cause was an exception escaping `execute_job`, which the
poll loop caught, logged as a poll-loop error, and forgot. The job was never
failed and never finished.

The exception came from `hashFiles` being handed an absolute pattern, which
`Path.glob` refuses. The agent action builds one with
`format('{0}/go.mod', inputs.target-repo)`.
"""

import importlib.util
from pathlib import Path


RUNNER_PATH = (
    Path(__file__).parents[2] / "src" / "runners" / "emulator" / "runner.py"
)
_SPEC = importlib.util.spec_from_file_location("github_emulator_runner", RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner_module)


def _runner():
    client = runner_module.RunnerClient.__new__(runner_module.RunnerClient)
    client._masks = set()
    return client


# --- hashFiles ------------------------------------------------------------

def test_a_relative_pattern_still_works(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    (tmp_path / "target-repo").mkdir()
    (tmp_path / "target-repo" / "go.mod").write_text("module x\n")
    assert runner_module._hash_files(["target-repo/go.mod"]) != ""


def test_an_absolute_pattern_inside_the_workspace_matches(monkeypatch, tmp_path):
    """The shape the agent action builds, which used to raise."""
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    (tmp_path / "target-repo").mkdir()
    (tmp_path / "target-repo" / "go.mod").write_text("module x\n")
    assert runner_module._hash_files([str(tmp_path / "target-repo" / "go.mod")]) != ""


def test_an_absolute_pattern_outside_the_workspace_matches_nothing(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path / "workspace"))
    (tmp_path / "workspace").mkdir()
    assert runner_module._hash_files(["/etc/hostname"]) == ""


def test_no_match_is_empty_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    assert runner_module._hash_files(["absent/go.mod"]) == ""
    assert runner_module._hash_files([str(tmp_path / "absent" / "go.mod")]) == ""


def test_a_condition_using_the_agent_actions_shape_is_evaluated(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    condition = "hashFiles(format('{0}/go.mod', 'target-repo')) != ''"
    assert not runner_module._evaluate_step_if(condition, {})
    (tmp_path / "target-repo").mkdir()
    (tmp_path / "target-repo" / "go.mod").write_text("module x\n")
    assert runner_module._evaluate_step_if(condition, {})


# --- a crash must end the job --------------------------------------------

def test_a_crashing_job_is_failed_and_says_why(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    client = _runner()
    uploaded: list[str] = []
    completed: list[tuple] = []
    client._upload_logs = lambda repository, job_id, chunk: uploaded.append(chunk)
    client._complete_job = (
        lambda repository, job_id, conclusion, steps, step_outputs=None:
        completed.append((job_id, conclusion))
    )

    def explode(_job):
        raise RuntimeError("something escaped")

    monkeypatch.setattr(client, "execute_job", explode)
    client._execute_job_guarded({"job_id": 7, "repository": "o/r", "steps": []})

    assert completed == [(7, "failure")], "the job must not be left in progress"
    assert any("something escaped" in chunk for chunk in uploaded), (
        "the reason must reach the run's own log"
    )


def test_the_guard_does_not_swallow_a_normal_job(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    client = _runner()
    ran: list[dict] = []
    monkeypatch.setattr(client, "execute_job", lambda job: ran.append(job))
    client._execute_job_guarded({"job_id": 8, "repository": "o/r", "steps": []})
    assert len(ran) == 1


def test_reporting_failures_do_not_mask_the_crash(monkeypatch, tmp_path):
    """If the emulator is also down, the crash still must not hang the runner."""
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    client = _runner()

    def unavailable(*_args, **_kwargs):
        raise ConnectionError("emulator unreachable")

    client._upload_logs = unavailable
    client._complete_job = unavailable
    monkeypatch.setattr(
        client, "execute_job", lambda job: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    client._execute_job_guarded({"job_id": 9, "repository": "o/r", "steps": []})
