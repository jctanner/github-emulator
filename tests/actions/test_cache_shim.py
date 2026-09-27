"""The locally emulated actions/cache family.

A self-hosted runner has no hosted cache service; what it has is a disk.
The shim keeps entries under a directory on the runner pod, so a restore
hits for as long as the pod lives, and reproduces the action's contract:
exact-key hit, restore-keys prefix fallback, the three outputs, and the
combined form's post-job save on a miss. Fullsend's reusable dispatch wraps
its CLI install in restore/save once a repository registers agents, which
is what made the whole harness-dispatch job fail as unsupported.
"""

import importlib.util
import os
import shutil
from pathlib import Path

import pytest


RUNNER_PATH = (
    Path(__file__).parents[2] / "src" / "runners" / "emulator" / "runner.py"
)
_SPEC = importlib.util.spec_from_file_location("github_emulator_runner_cache", RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner_module)

SHA = "55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
RESTORE = f"actions/cache/restore@{SHA}"
SAVE = f"actions/cache/save@{SHA}"
COMBINED = f"actions/cache@{SHA}"


@pytest.fixture
def runner(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(runner_module, "WORKDIR", str(workspace))
    monkeypatch.delenv("RUNNER_CACHE_DIR", raising=False)
    client = runner_module.RunnerClient.__new__(runner_module.RunnerClient)
    client._masks = set()
    client.workspace = workspace
    return client


def _step(uses, **inputs):
    return {"uses": uses, "with": inputs}


def _shim(runner, step, job=None):
    name = runner_module._ACTION_SHIMS[step["uses"].split("@", 1)[0]]
    return getattr(runner, name)(step, step["uses"], job if job is not None else {})


def test_all_three_forms_are_registered():
    for uses in ("actions/cache", "actions/cache/restore", "actions/cache/save"):
        assert runner_module._ACTION_SHIMS[uses] == "_shim_cache"


def test_restore_on_an_empty_cache_is_a_miss_not_a_failure(runner):
    step = _step(RESTORE, path="tool/bin", key="cli-abc")
    result, output, updates = _shim(runner, step)
    assert result == "success"
    assert "Cache not found" in output
    assert step["outputs"] == {
        "cache-hit": "false",
        "cache-primary-key": "cli-abc",
        "cache-matched-key": "",
    }
    assert updates == {}


def test_save_then_restore_round_trips_files_and_directories(runner):
    (runner.workspace / "tool" / "bin").mkdir(parents=True)
    (runner.workspace / "tool" / "bin" / "fullsend").write_text("binary")
    (runner.workspace / "single.txt").write_text("one")

    save = _step(SAVE, path="tool/bin\nsingle.txt", key="cli-abc")
    result, output, _ = _shim(runner, save)
    assert result == "success"
    assert "Cache saved with key: cli-abc" in output
    assert "outputs" not in save  # save has no outputs

    # A later job on the same pod starts from a removed workspace; the
    # cache is a sibling and survives.
    shutil.rmtree(runner.workspace)
    runner.workspace.mkdir()
    assert (runner.workspace.parent / "_cache").is_dir()

    restore = _step(RESTORE, path="tool/bin\nsingle.txt", key="cli-abc")
    result, output, _ = _shim(runner, restore)
    assert result == "success"
    assert "Cache restored from key: cli-abc" in output
    assert restore["outputs"]["cache-hit"] == "true"
    assert restore["outputs"]["cache-matched-key"] == "cli-abc"
    assert (runner.workspace / "tool" / "bin" / "fullsend").read_text() == "binary"
    assert (runner.workspace / "single.txt").read_text() == "one"


def test_restore_keys_fall_back_to_the_newest_prefix_match(runner):
    (runner.workspace / "bin").mkdir()
    (runner.workspace / "bin" / "x").write_text("old")
    _shim(runner, _step(SAVE, path="bin", key="cli-per-repo-aaa"))
    # Ensure the second entry is strictly newer on coarse filesystems.
    root = runner._cache_root()
    for slot in root.iterdir():
        os.utime(slot / "manifest.json", (1, 1))
    (runner.workspace / "bin" / "x").write_text("new")
    _shim(runner, _step(SAVE, path="bin", key="cli-per-repo-bbb"))

    step = _step(RESTORE, path="bin", key="cli-per-repo-ccc", **{"restore-keys": "cli-org-\ncli-per-repo-"})
    result, _, _ = _shim(runner, step)
    assert result == "success"
    assert step["outputs"] == {
        "cache-hit": "false",  # partial match is not a hit, as on GitHub
        "cache-primary-key": "cli-per-repo-ccc",
        "cache-matched-key": "cli-per-repo-bbb",
    }
    assert (runner.workspace / "bin" / "x").read_text() == "new"


def test_saving_an_existing_key_is_a_no_op(runner):
    (runner.workspace / "bin").mkdir()
    (runner.workspace / "bin" / "x").write_text("first")
    _shim(runner, _step(SAVE, path="bin", key="k"))
    (runner.workspace / "bin" / "x").write_text("second")
    _, output, _ = _shim(runner, _step(SAVE, path="bin", key="k"))
    assert "already exists" in output
    step = _step(RESTORE, path="bin", key="k")
    _shim(runner, step)
    assert (runner.workspace / "bin" / "x").read_text() == "first"


def test_missing_paths_are_reported_and_nothing_is_cached(runner):
    _, output, _ = _shim(runner, _step(SAVE, path="does/not/exist", key="k"))
    assert "Path does not exist" in output
    assert "nothing cached" in output
    step = _step(RESTORE, path="does/not/exist", key="k")
    _shim(runner, step)
    assert step["outputs"]["cache-hit"] == "false"


def test_key_and_path_are_required(runner):
    result, output, _ = _shim(runner, _step(RESTORE, key="k"))
    assert result == "failure"
    assert "'key' and 'path'" in output


def test_fail_on_cache_miss_fails_the_step(runner):
    step = _step(RESTORE, path="bin", key="k", **{"fail-on-cache-miss": "true"})
    result, _, _ = _shim(runner, step)
    assert result == "failure"


def test_combined_form_saves_after_the_job_only_on_a_miss(runner):
    job = {}
    step = _step(COMBINED, path="bin", key="k")
    result, _, _ = _shim(runner, step, job)
    assert result == "success"
    assert step["outputs"]["cache-hit"] == "false"
    assert job["_deferred_cache_saves"] == [("k", ["bin"])]

    # The step's own body produces the files; the post step captures them.
    (runner.workspace / "bin").mkdir()
    (runner.workspace / "bin" / "x").write_text("built")
    runner._flush_deferred_cache_saves(job)
    assert "_deferred_cache_saves" not in job

    hit = _step(COMBINED, path="bin", key="k")
    _shim(runner, hit, job)
    assert hit["outputs"]["cache-hit"] == "true"
    assert "_deferred_cache_saves" not in job


def test_cache_root_honours_runner_cache_dir(runner, monkeypatch, tmp_path):
    monkeypatch.setenv("RUNNER_CACHE_DIR", str(tmp_path / "elsewhere"))
    assert runner._cache_root() == tmp_path / "elsewhere"
    (runner.workspace / "bin").mkdir()
    (runner.workspace / "bin" / "x").write_text("x")
    _shim(runner, _step(SAVE, path="bin", key="k"))
    assert any((tmp_path / "elsewhere").iterdir())
