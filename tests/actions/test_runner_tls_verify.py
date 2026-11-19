"""The Python runner verifies the emulator's TLS certificate unless told not to."""

import importlib.util
from pathlib import Path

import pytest

RUNNER_PATH = Path(__file__).parents[2] / "src" / "runners" / "emulator" / "runner.py"
_SPEC = importlib.util.spec_from_file_location("github_emulator_runner_tls", RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner_module)


def _verifies(monkeypatch, value):
    seen = {}

    class Client:
        def __init__(self, *, verify, timeout):
            seen["verify"] = verify

    monkeypatch.setattr(runner_module.httpx, "Client", Client)
    if value is None:
        monkeypatch.delenv("RUNNER_TLS_VERIFY", raising=False)
    else:
        monkeypatch.setenv("RUNNER_TLS_VERIFY", value)
    runner_module.RunnerClient()
    return seen["verify"]


def test_certificates_are_verified_by_default(monkeypatch):
    assert _verifies(monkeypatch, None) is True


@pytest.mark.parametrize("value", ["false", "0", "no", "FALSE"])
def test_verification_can_be_turned_off(monkeypatch, value):
    assert _verifies(monkeypatch, value) is False


def test_other_values_keep_verification_on(monkeypatch):
    assert _verifies(monkeypatch, "true") is True
