"""Locally emulated third-party actions.

This runner cannot fetch or execute a marketplace action, and refusing one by
name is the default. A short list of environment-setup actions is emulated
instead, because the local stack can reproduce the effect later steps depend
on. These tests pin both halves: that the emulation exports what those steps
read, and that it fails loudly rather than exporting nothing and reporting
success.
"""

import importlib.util
import json
from pathlib import Path


RUNNER_PATH = (
    Path(__file__).parents[2] / "src" / "runners" / "emulator" / "runner.py"
)
_SPEC = importlib.util.spec_from_file_location("github_emulator_runner", RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner_module)

AUTH = "google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093"


def _runner():
    client = runner_module.RunnerClient.__new__(runner_module.RunnerClient)
    client._masks = set()
    return client


def _with_credentials(monkeypatch, tmp_path, payload):
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(runner_module, "GCP_CREDENTIALS_FILE", str(path))
    return path


def test_service_account_key_exports_the_paths_downstream_steps_read(
    monkeypatch, tmp_path
):
    path = _with_credentials(
        monkeypatch, tmp_path, {"type": "service_account", "project_id": "proj-from-file"}
    )
    step = {"uses": AUTH, "with": {}}
    result, output, updates = _runner()._shim_google_auth(step, AUTH)

    assert result == "success"
    assert updates["GOOGLE_APPLICATION_CREDENTIALS"] == str(path)
    # The composite action that wraps this one masks these two by name.
    assert updates["GOOGLE_GHA_CREDS_PATH"] == str(path)
    assert updates["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] == str(path)
    assert updates["GOOGLE_CLOUD_PROJECT"] == "proj-from-file"
    assert "service_account" in output


def test_the_actions_project_id_input_wins(monkeypatch, tmp_path):
    _with_credentials(
        monkeypatch, tmp_path, {"type": "service_account", "project_id": "proj-from-file"}
    )
    step = {"uses": AUTH, "with": {"project_id": "proj-from-input"}}
    _result, _output, updates = _runner()._shim_google_auth(step, AUTH)
    assert updates["GOOGLE_CLOUD_PROJECT"] == "proj-from-input"


def test_authorized_user_falls_back_to_its_quota_project(monkeypatch, tmp_path):
    """An authorized-user credential carries no project_id, as gcloud writes it."""
    _with_credentials(
        monkeypatch,
        tmp_path,
        {"type": "authorized_user", "quota_project_id": "quota-proj"},
    )
    step = {"uses": AUTH, "with": {}}
    _result, _output, updates = _runner()._shim_google_auth(step, AUTH)
    assert updates["GOOGLE_CLOUD_PROJECT"] == "quota-proj"


def test_a_credential_that_is_not_federated_satisfies_fullsends_own_script(
    monkeypatch, tmp_path
):
    """prepare-sandbox-credentials.sh no-ops for any type but external_account."""
    _with_credentials(monkeypatch, tmp_path, {"type": "authorized_user"})
    step = {"uses": AUTH, "with": {}}
    result, _output, updates = _runner()._shim_google_auth(step, AUTH)
    assert result == "success"
    assert json.loads(
        Path(updates["GOOGLE_APPLICATION_CREDENTIALS"]).read_text()
    )["type"] != "external_account"


def test_missing_credentials_fail_rather_than_export_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        runner_module, "GCP_CREDENTIALS_FILE", str(tmp_path / "absent.json")
    )
    step = {"uses": AUTH, "with": {}}
    result, output, updates = _runner()._shim_google_auth(step, AUTH)
    assert result == "failure"
    assert updates == {}
    assert "No credentials file" in output


def test_a_federated_credential_is_refused_with_its_reason(monkeypatch, tmp_path):
    """Its credential source is a URL this stack cannot reach."""
    _with_credentials(
        monkeypatch,
        tmp_path,
        {"type": "external_account", "credential_source": {"url": "https://example"}},
    )
    step = {"uses": AUTH, "with": {}}
    result, output, updates = _runner()._shim_google_auth(step, AUTH)
    assert result == "failure"
    assert updates == {}
    assert "external_account" in output


def test_unreadable_credentials_fail_with_the_path(monkeypatch, tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text("{not json")
    monkeypatch.setattr(runner_module, "GCP_CREDENTIALS_FILE", str(path))
    result, output, _updates = _runner()._shim_google_auth({"uses": AUTH}, AUTH)
    assert result == "failure"
    assert str(path) in output


def test_the_credentials_path_is_masked(monkeypatch, tmp_path):
    path = _with_credentials(monkeypatch, tmp_path, {"type": "service_account"})
    client = _runner()
    client._shim_google_auth({"uses": AUTH, "with": {}}, AUTH)
    assert str(path) in client._masks


def test_an_unlisted_action_is_still_refused_by_name(monkeypatch, tmp_path):
    """The shim list is an exception, not a general action runtime."""
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    client = _runner()
    result, output, _updates = client._run_step(
        {"uses": "actions/setup-node@v4", "name": "Setup node"}, {}, {}
    )
    assert result == "failure"
    assert "Unsupported action: actions/setup-node@v4" in output
    # The message names what it can do, including the emulated list.
    assert "google-github-actions/auth" in output


def test_the_shim_matches_on_name_not_version(monkeypatch, tmp_path):
    """A version bump should show in the log, not become 'unsupported'."""
    assert "google-github-actions/auth" in runner_module._ACTION_SHIMS
    for ref in (
        "google-github-actions/auth@v2",
        "google-github-actions/auth@v3",
        "google-github-actions/auth@" + "a" * 40,
    ):
        assert runner_module._ACTION_SHIMS.get(ref.split("@", 1)[0])
