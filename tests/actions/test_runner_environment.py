"""The standard runner variables a step is entitled to.

The runner set only a subset of them, and the missing ones failed far from
their cause: a step exited with `RUNNER_TEMP: unbound variable` several jobs
into a dispatch. Two of the four are behaviours rather than values.
``GITHUB_PATH`` is how a step puts a binary where later steps can run it, and
``GITHUB_ACTION_PATH`` is how a composite action's step finds scripts shipped
beside it.
"""

import importlib.util
import os
from pathlib import Path


RUNNER_PATH = (
    Path(__file__).parents[2] / "src" / "runners" / "emulator" / "runner.py"
)
_SPEC = importlib.util.spec_from_file_location("github_emulator_runner", RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner_module)


def _runner(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path / "workspace"))
    monkeypatch.setattr(runner_module, "RUNNER_TEMP", str(tmp_path / "_temp"))
    client = runner_module.RunnerClient.__new__(runner_module.RunnerClient)
    client._masks = set()
    return client


def _run(client, steps, runtime_env=None):
    """Run steps through execute_job and return the combined log."""
    chunks: list[str] = []
    client._upload_logs = lambda repository, job_id, chunk: chunks.append(chunk)
    client._report_progress = lambda repository, job_id, steps: None
    client._complete_job = (
        lambda repository, job_id, conclusion, steps, step_outputs=None: None
    )
    client.execute_job({
        "job_id": 1,
        "name": "environment",
        "token": "job-token",
        "steps": steps,
        "timeout_seconds": 30,
    })
    return "".join(chunks)


def _echo(number, name, script):
    return {"number": number, "name": name, "run": script, "shell": "bash"}


def test_runner_temp_exists_and_is_writable(monkeypatch, tmp_path):
    client = _runner(monkeypatch, tmp_path)
    log = _run(client, [
        _echo(1, "Use temp", 'echo "TEMP=${RUNNER_TEMP}"; touch "${RUNNER_TEMP}/probe"'),
        _echo(2, "Read it back", 'test -f "${RUNNER_TEMP}/probe" && echo "PERSISTED=yes"'),
    ])
    assert f"TEMP={tmp_path / '_temp'}" in log
    assert "PERSISTED=yes" in log


def test_runner_temp_is_outside_the_workspace(monkeypatch, tmp_path):
    """A checkout makes the workspace root a git tree; temp must not be in it."""
    client = _runner(monkeypatch, tmp_path)
    workspace = Path(runner_module.WORKDIR).resolve()
    temp = Path(runner_module.RUNNER_TEMP).resolve()
    _run(client, [_echo(1, "Touch", 'touch "${RUNNER_TEMP}/probe"')])
    assert not str(temp).startswith(str(workspace) + os.sep)


def test_runner_temp_starts_empty_for_each_job(monkeypatch, tmp_path):
    """GitHub hands every job an empty one; a cache must not outlive its job."""
    client = _runner(monkeypatch, tmp_path)
    _run(client, [_echo(1, "Leave something", 'touch "${RUNNER_TEMP}/stale"')])
    log = _run(client, [
        _echo(1, "Look for it", (
            'test -e "${RUNNER_TEMP}/stale" '
            '&& echo "STALE=survived" || echo "STALE=cleared"'
        ))
    ])
    assert "STALE=cleared" in log
    assert "STALE=survived" not in log


def test_runner_arch_uses_githubs_spelling(monkeypatch, tmp_path):
    client = _runner(monkeypatch, tmp_path)
    log = _run(client, [_echo(1, "Arch", 'echo "ARCH=${RUNNER_ARCH}"')])
    assert "ARCH=X64" in log or "ARCH=ARM64" in log


def test_arch_mapping_is_githubs_not_unames():
    assert runner_module._RUNNER_ARCH_BY_MACHINE["x86_64"] == "X64"
    assert runner_module._RUNNER_ARCH_BY_MACHINE["aarch64"] == "ARM64"


def test_github_path_puts_a_binary_on_path_for_later_steps(monkeypatch, tmp_path):
    """The behaviour every Fullsend install path depends on."""
    client = _runner(monkeypatch, tmp_path)
    log = _run(client, [
        _echo(1, "Install", (
            'mkdir -p "${RUNNER_TEMP}/bin"; '
            'printf "#!/bin/sh\\necho ran-the-installed-binary\\n" '
            '> "${RUNNER_TEMP}/bin/demo"; '
            'chmod +x "${RUNNER_TEMP}/bin/demo"; '
            'echo "${RUNNER_TEMP}/bin" >> "${GITHUB_PATH}"'
        )),
        _echo(2, "Use it", "demo"),
    ])
    assert "ran-the-installed-binary" in log


def test_github_path_does_not_leak_into_the_step_that_wrote_it(
    monkeypatch, tmp_path
):
    """GitHub applies it to later steps, not the current one."""
    client = _runner(monkeypatch, tmp_path)
    log = _run(client, [
        _echo(1, "Write and try", (
            'mkdir -p "${RUNNER_TEMP}/bin"; '
            'printf "#!/bin/sh\\necho too-early\\n" > "${RUNNER_TEMP}/bin/later"; '
            'chmod +x "${RUNNER_TEMP}/bin/later"; '
            'echo "${RUNNER_TEMP}/bin" >> "${GITHUB_PATH}"; '
            'command -v later >/dev/null && echo "VISIBLE=yes" || echo "VISIBLE=no"'
        )),
        _echo(2, "Now it is there", 'command -v later >/dev/null && echo "VISIBLE=yes"'),
    ])
    assert "VISIBLE=no" in log
    assert "VISIBLE=yes" in log


def test_github_path_accumulates_across_steps(monkeypatch, tmp_path):
    client = _runner(monkeypatch, tmp_path)
    log = _run(client, [
        _echo(1, "First", (
            'mkdir -p "${RUNNER_TEMP}/one"; '
            'printf "#!/bin/sh\\necho one\\n" > "${RUNNER_TEMP}/one/first"; '
            'chmod +x "${RUNNER_TEMP}/one/first"; '
            'echo "${RUNNER_TEMP}/one" >> "${GITHUB_PATH}"'
        )),
        _echo(2, "Second", (
            'mkdir -p "${RUNNER_TEMP}/two"; '
            'printf "#!/bin/sh\\necho two\\n" > "${RUNNER_TEMP}/two/second"; '
            'chmod +x "${RUNNER_TEMP}/two/second"; '
            'echo "${RUNNER_TEMP}/two" >> "${GITHUB_PATH}"'
        )),
        _echo(3, "Both", "first; second"),
    ])
    assert "one" in log and "two" in log


def test_github_action_path_points_at_the_running_composite_action(
    monkeypatch, tmp_path
):
    client = _runner(monkeypatch, tmp_path)
    action = Path(runner_module.WORKDIR) / "vendor" / "demo-action"
    action.mkdir(parents=True)
    (action / "action.yml").write_text(
        "name: Demo\n"
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - shell: bash\n"
        '      run: echo "ACTION_PATH=${GITHUB_ACTION_PATH}"; '
        'cat "${GITHUB_ACTION_PATH}/marker.txt"\n'
    )
    (action / "marker.txt").write_text("found-my-own-file\n")

    log = _run(client, [{"number": 1, "name": "Demo", "uses": "./vendor/demo-action"}])
    assert f"ACTION_PATH={action}" in log
    assert "found-my-own-file" in log


def test_github_action_path_is_unset_for_a_workflow_step(monkeypatch, tmp_path):
    """GitHub does not set it outside an action, and a stale one misleads."""
    client = _runner(monkeypatch, tmp_path)
    monkeypatch.setenv("GITHUB_ACTION_PATH", "/leftover/from/the/pod")
    log = _run(client, [
        _echo(1, "Plain", 'echo "ACTION_PATH=${GITHUB_ACTION_PATH:-unset}"')
    ])
    assert "ACTION_PATH=unset" in log
