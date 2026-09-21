"""The Actions OIDC variables the bundled runner exposes to a step.

The runner used to serve OIDC itself from a loopback broker that handed back
one static string held in the pod environment. Every job on that runner got the
same credential, so a mint could not tell which run was asking. These tests pin
the replacement: the request token is the job's own token, the URL points at the
emulator, and a job that did not ask for ``id-token: write`` gets neither, even
when the pod environment carries leftovers.
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


def _captured_environment(monkeypatch, tmp_path, permissions):
    """Run one step that prints its OIDC variables and return them."""
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    # Leftovers a pod environment might still carry.
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "stale-shared-secret")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "http://127.0.0.1:8765/oidc")

    runner = runner_module.RunnerClient()
    chunks: list[str] = []
    runner._upload_logs = lambda repository, job_id, chunk: chunks.append(chunk)
    runner._report_progress = lambda repository, job_id, steps: None
    runner._complete_job = (
        lambda repository, job_id, conclusion, steps, step_outputs=None: None
    )

    runner.execute_job({
        "job_id": 11,
        "name": "mint",
        "token": "job-scoped-token",
        "permissions": permissions,
        "steps": [{
            "number": 1,
            "name": "Show OIDC",
            "run": (
                'echo "URL=${ACTIONS_ID_TOKEN_REQUEST_URL:-unset}"; '
                'echo "TOKEN=${ACTIONS_ID_TOKEN_REQUEST_TOKEN:-unset}"'
            ),
            "shell": "bash",
        }],
        "timeout_seconds": 10,
    })
    combined = "".join(chunks)
    return dict(
        line.split("=", 1)
        for line in combined.splitlines()
        if line.startswith(("URL=", "TOKEN="))
    )


def test_request_token_is_the_jobs_own_token(monkeypatch, tmp_path):
    captured = _captured_environment(
        monkeypatch, tmp_path, {"id-token": "write", "issues": "write"}
    )
    assert captured["TOKEN"] == "job-scoped-token"
    assert captured["TOKEN"] != "stale-shared-secret"


def test_request_url_points_at_the_emulator_with_a_query_string(monkeypatch, tmp_path):
    captured = _captured_environment(
        monkeypatch, tmp_path, {"id-token": "write"}
    )
    # The mint action appends "&audience=...", so the URL must already carry a
    # query string or the request loses its audience.
    assert "?" in captured["URL"]
    assert captured["URL"].startswith(runner_module.EMULATOR_URL)
    assert "/actions/oidc/token" in captured["URL"]


def test_job_without_id_token_write_gets_nothing(monkeypatch, tmp_path):
    captured = _captured_environment(
        monkeypatch, tmp_path, {"contents": "read", "issues": "read"}
    )
    assert captured["URL"] == "unset"
    assert captured["TOKEN"] == "unset"


def test_job_with_no_permissions_block_may_still_mint(monkeypatch, tmp_path):
    """Matches the documented permissive default for an absent block."""
    captured = _captured_environment(monkeypatch, tmp_path, None)
    assert captured["TOKEN"] == "job-scoped-token"
