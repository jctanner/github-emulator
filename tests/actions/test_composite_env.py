"""A composite action's steps see the env the calling step set.

GitHub applies the calling step's `env:` to every step of the action it calls.
The runner dropped it, so a value a workflow set for an action to read never
arrived, and the failure surfaced inside the action as a missing variable with
no hint that a caller had supplied it. The real case was the agent step's
REPO_FULL_NAME, which the CLI needs to decide which repositories a token may
be minted for.
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


def _action(tmp_path, body):
    action = Path(runner_module.WORKDIR) / "act"
    action.mkdir(parents=True, exist_ok=True)
    (action / "action.yml").write_text(body)
    return action


def _run(monkeypatch, tmp_path, action_body, step):
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    monkeypatch.setattr(runner_module, "RUNNER_TEMP", str(tmp_path / "_temp"))
    Path(runner_module.RUNNER_TEMP).mkdir(parents=True, exist_ok=True)
    _action(tmp_path, action_body)
    client = runner_module.RunnerClient.__new__(runner_module.RunnerClient)
    client._masks = set()
    chunks: list[str] = []
    result, _output, _updates = client._composite_step(
        step, {}, {}, log_callback=chunks.append,
    )
    return result, "".join(chunks)


ECHO_ACTION = (
    "name: Demo\n"
    "runs:\n"
    "  using: composite\n"
    "  steps:\n"
    "    - shell: bash\n"
    '      run: echo "SAW=${REPO_FULL_NAME:-unset}"\n'
)


def test_the_calling_steps_env_reaches_the_action(monkeypatch, tmp_path):
    result, log = _run(
        monkeypatch, tmp_path, ECHO_ACTION,
        {"uses": "./act", "env": {"REPO_FULL_NAME": "acme/widgets"}},
    )
    assert result == "success"
    assert "SAW=acme/widgets" in log


def test_without_a_calling_env_nothing_is_invented(monkeypatch, tmp_path):
    result, log = _run(monkeypatch, tmp_path, ECHO_ACTION, {"uses": "./act"})
    assert result == "success"
    assert "SAW=unset" in log


def test_the_actions_own_env_wins(monkeypatch, tmp_path):
    """An action that sets a value deliberately is not overridden by a caller."""
    action = (
        "name: Demo\n"
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - shell: bash\n"
        "      env:\n"
        "        REPO_FULL_NAME: from-the-action\n"
        '      run: echo "SAW=${REPO_FULL_NAME:-unset}"\n'
    )
    result, log = _run(
        monkeypatch, tmp_path, action,
        {"uses": "./act", "env": {"REPO_FULL_NAME": "from-the-caller"}},
    )
    assert result == "success"
    assert "SAW=from-the-action" in log
