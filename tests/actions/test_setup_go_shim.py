"""The locally emulated actions/setup-go.

Unlike the Google auth emulation, this one reproduces what the real action
does rather than substituting for something unreachable: resolve a version,
make that toolchain available, and put it on PATH. Hosted runners answer from a
preinstalled tool cache and download only on a miss; this answers from the
toolchain baked into the runner image and does the same.
"""

import importlib.util
import os
from pathlib import Path

import pytest


RUNNER_PATH = (
    Path(__file__).parents[2] / "src" / "runners" / "emulator" / "runner.py"
)
_SPEC = importlib.util.spec_from_file_location("github_emulator_runner", RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner_module)

SETUP_GO = "actions/setup-go@924ae3a1cded613372ab5595356fb5720e22ba16"


def _runner():
    client = runner_module.RunnerClient.__new__(runner_module.RunnerClient)
    client._masks = set()
    return client


def _fake_goroot(monkeypatch, tmp_path, version):
    """Stand in for the toolchain baked into the image."""
    root = tmp_path / "goroot"
    (root / "bin").mkdir(parents=True)
    go = root / "bin" / "go"
    go.write_text(
        "#!/bin/sh\n"
        f'[ "$1" = version ] && echo "go version go{version} linux/amd64"\n'
    )
    go.chmod(0o755)
    monkeypatch.setattr(runner_module, "GO_ROOT", str(root))
    return root


def _go_mod(tmp_path, body):
    monkeypatch_workdir = tmp_path / "workspace"
    monkeypatch_workdir.mkdir(exist_ok=True)
    path = monkeypatch_workdir / "go.mod"
    path.write_text(body)
    return path


# --- version resolution ----------------------------------------------------

def test_go_version_input_wins_over_the_file(tmp_path):
    path = _go_mod(tmp_path, "module x\n\ngo 1.20.0\n")
    version, source = runner_module._requested_go_version(
        {"go-version": "1.26.5", "go-version-file": str(path)}, tmp_path
    )
    assert version == "1.26.5"
    assert source == "go-version"


def test_version_is_read_from_a_go_mod_directive(tmp_path):
    path = _go_mod(tmp_path, "module fullsend\n\ngo 1.26.5\n\nrequire (\n)\n")
    version, source = runner_module._requested_go_version(
        {"go-version-file": str(path)}, tmp_path
    )
    assert version == "1.26.5"
    assert source == str(path)


def test_a_toolchain_directive_wins_over_the_go_directive(tmp_path):
    path = _go_mod(tmp_path, "module x\n\ngo 1.24.0\n\ntoolchain go1.26.5\n")
    version, _source = runner_module._requested_go_version(
        {"go-version-file": str(path)}, tmp_path
    )
    assert version == "1.26.5"


def test_a_bare_version_file_is_read_whole(tmp_path):
    path = tmp_path / ".go-version"
    path.write_text("1.26.5\n")
    version, _source = runner_module._requested_go_version(
        {"go-version-file": str(path)}, tmp_path
    )
    assert version == "1.26.5"


def test_a_relative_version_file_resolves_against_the_workspace(tmp_path):
    (tmp_path / "fullsend-src").mkdir()
    (tmp_path / "fullsend-src" / "go.mod").write_text("module x\n\ngo 1.26.5\n")
    version, _source = runner_module._requested_go_version(
        {"go-version-file": "fullsend-src/go.mod"}, tmp_path
    )
    assert version == "1.26.5"


def test_a_missing_version_file_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        runner_module._requested_go_version(
            {"go-version-file": "absent/go.mod"}, tmp_path
        )


def test_version_comparison_is_numeric_not_lexical():
    parse = runner_module._parse_go_version
    assert parse("1.26.5") > parse("1.9.0")
    assert parse("go1.26.5") == parse("1.26.5")
    assert parse("1.26") < parse("1.26.5")


# --- selection -------------------------------------------------------------

def test_the_image_toolchain_is_used_when_it_satisfies_the_request(
    monkeypatch, tmp_path
):
    root = _fake_goroot(monkeypatch, tmp_path, "1.26.5")
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    step = {"uses": SETUP_GO, "with": {"go-version": "1.26.5"}}
    result, output, updates = _runner()._shim_setup_go(step, SETUP_GO, {})

    assert result == "success"
    assert updates["PATH"].split(os.pathsep)[0] == str(root / "bin")
    assert updates["GOROOT"] == str(root)
    assert step["outputs"]["go-version"] == "1.26.5"
    assert "satisfies the request" in output


def test_a_newer_image_toolchain_also_satisfies_an_older_request(
    monkeypatch, tmp_path
):
    _fake_goroot(monkeypatch, tmp_path, "1.26.5")
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    step = {"uses": SETUP_GO, "with": {"go-version": "1.24.0"}}
    result, _output, updates = _runner()._shim_setup_go(step, SETUP_GO, {})
    assert result == "success"
    assert "GOROOT" in updates


def test_a_newer_request_is_delegated_to_go_and_said_out_loud(
    monkeypatch, tmp_path
):
    """A silent auto-upgrade certifies a toolchain nobody chose."""
    _fake_goroot(monkeypatch, tmp_path, "1.26.5")
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    downloads = []
    monkeypatch.setattr(
        runner_module.RunnerClient,
        "_download_go",
        lambda self, version: downloads.append(version) or (None, "should not run"),
    )
    step = {"uses": SETUP_GO, "with": {"go-version": "1.27.0"}}
    result, output, _updates = _runner()._shim_setup_go(step, SETUP_GO, {})

    assert result == "success"
    assert downloads == [], "toolchain switching should avoid the download"
    assert "older than the requested 1.27.0" in output


def test_a_toolchain_too_old_to_switch_falls_back_to_downloading(
    monkeypatch, tmp_path
):
    _fake_goroot(monkeypatch, tmp_path, "1.19.0")
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    fetched = tmp_path / "fetched" / "go" / "bin"
    fetched.mkdir(parents=True)
    (fetched / "go").write_text("")
    monkeypatch.setattr(
        runner_module.RunnerClient,
        "_download_go",
        lambda self, version: (fetched, f"downloaded go{version}"),
    )
    step = {"uses": SETUP_GO, "with": {"go-version": "1.26.5"}}
    result, output, updates = _runner()._shim_setup_go(step, SETUP_GO, {})

    assert result == "success"
    assert updates["PATH"].split(os.pathsep)[0] == str(fetched)
    assert "downloaded go1.26.5" in output


def test_no_image_toolchain_and_no_request_fails_rather_than_guessing(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(runner_module, "GO_ROOT", str(tmp_path / "absent"))
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    result, output, updates = _runner()._shim_setup_go(
        {"uses": SETUP_GO, "with": {}}, SETUP_GO, {}
    )
    assert result == "failure"
    assert updates == {}
    assert "nothing to select" in output


def test_a_failed_download_is_reported_not_swallowed(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_module, "GO_ROOT", str(tmp_path / "absent"))
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    monkeypatch.setattr(
        runner_module.RunnerClient,
        "_download_go",
        lambda self, version: (None, "could not download https://example: boom"),
    )
    result, output, updates = _runner()._shim_setup_go(
        {"uses": SETUP_GO, "with": {"go-version": "1.26.5"}}, SETUP_GO, {}
    )
    assert result == "failure"
    assert updates == {}
    assert "boom" in output


def test_the_missing_go_mod_case_fails_with_the_path(monkeypatch, tmp_path):
    _fake_goroot(monkeypatch, tmp_path, "1.26.5")
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    result, output, _updates = _runner()._shim_setup_go(
        {"uses": SETUP_GO, "with": {"go-version-file": "fullsend-src/go.mod"}},
        SETUP_GO,
        {},
    )
    assert result == "failure"
    assert "go-version-file not found" in output


def test_setup_go_is_registered_by_name_not_version():
    assert runner_module._ACTION_SHIMS.get("actions/setup-go")
    assert runner_module._ACTION_SHIMS.get(SETUP_GO.split("@", 1)[0])
