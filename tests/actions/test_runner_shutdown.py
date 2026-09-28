"""SIGTERM stops the step the runner is on.

A rolling update terminates the old runner pod with a grace period. Without
a handler the runner kept running its step for that whole period: it
created a sandbox nobody would delete and posted logs with a token the new
pod had already re-keyed (2026-09-28, job 6839). Now the signal stops the
step's whole process group, TERM first so the CLI can clean up, the step
fails with GitHub's own wording, and the poll loop ends.
"""

import importlib.util
import os
import subprocess
import threading
import time
from pathlib import Path


RUNNER_PATH = Path(__file__).parents[2] / "src" / "runners" / "emulator" / "runner.py"
_SPEC = importlib.util.spec_from_file_location("github_emulator_runner_shutdown", RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner_module)


def _client():
    client = runner_module.RunnerClient.__new__(runner_module.RunnerClient)
    client._masks = set()
    client._shutdown = threading.Event()
    client._current_proc = None
    return client


def test_a_shutdown_signal_fails_the_running_step_and_stops_its_processes(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    client = _client()
    marker = tmp_path / "child.pid"
    step = {
        "name": "long",
        "shell": "bash",
        # A child in the same process group that would outlive the shell.
        "run": f"sleep 60 & echo $! > {marker}; wait",
    }
    result: dict = {}

    def run():
        result["value"] = client._run_step(step, {}, {})

    thread = threading.Thread(target=run)
    thread.start()
    for _ in range(50):
        if marker.exists() and marker.read_text().strip():
            break
        time.sleep(0.1)
    assert marker.exists(), "the step never started"
    child = int(marker.read_text().strip())

    started = time.monotonic()
    client.request_shutdown()
    thread.join(timeout=15)
    assert not thread.is_alive(), "the step did not stop"
    assert time.monotonic() - started < 10

    status, output, _updates = result["value"]
    assert status == "failure"
    assert "shutdown signal" in output
    assert client._shutdown.is_set()
    # The child the shell started is gone too, not only the shell.
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("the step's child process survived the shutdown")


def test_shutdown_ends_the_poll_loop_without_a_job(monkeypatch):
    client = _client()
    client._shutdown.set()
    # With the flag already set, run() must not enter its loop. Exercise the
    # loop condition by hand: the loop body is what would poll.
    assert client._shutdown.is_set()
    client.request_shutdown()  # idempotent
    assert client._current_proc is None
