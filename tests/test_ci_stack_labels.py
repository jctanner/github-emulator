"""scripts/ci-stack.sh's default runner labels follow the host's Ubuntu release."""

import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci-stack.sh"


def _labels_code():
    """default_labels() and the LABELS line, cut from the script.

    Running the whole script would start a stack; these lines are all the
    label default is.
    """
    text = SCRIPT.read_text()
    function = re.search(r"^default_labels\(\) \{\n.*?^\}\n", text, re.M | re.S)
    labels = re.search(r'^LABELS="\$\{CI_STACK_RUNNER_LABELS:-.*\}"\n', text, re.M)
    assert function and labels
    return function.group(0), labels.group(0)


def _default_labels(os_release: Path) -> str:
    function, _ = _labels_code()
    result = subprocess.run(
        ["bash", "-c", f'set -euo pipefail\n{function}default_labels "$1"', "_", str(os_release)],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        ('NAME="Ubuntu"\nID=ubuntu\nVERSION_ID="24.04"\n', "ubuntu-24.04"),
        ('NAME="Ubuntu"\nID=ubuntu\nVERSION_ID="26.04"\n', "ubuntu-26.04"),
        # No trailing newline, single quotes: still read.
        ("ID='ubuntu'\nVERSION_ID='26.04'", "ubuntu-26.04"),
        ('ID=debian\nVERSION_ID="12"\n', "ubuntu-24.04"),
        ('ID=fedora\nVERSION_ID=41\n', "ubuntu-24.04"),
        # A version that is not N.N does not reach the label list.
        ('ID=ubuntu\nVERSION_ID="26.04,windows"\n', "ubuntu-24.04"),
        ("", "ubuntu-24.04"),
    ],
)
def test_default_labels_follow_os_release(tmp_path, contents, expected):
    os_release = tmp_path / "os-release"
    os_release.write_text(contents)
    assert _default_labels(os_release) == f"self-hosted,linux,ubuntu-latest,{expected}"


def test_default_labels_without_os_release(tmp_path):
    assert _default_labels(tmp_path / "missing") == "self-hosted,linux,ubuntu-latest,ubuntu-24.04"


def test_runner_labels_setting_overrides_default():
    function, labels = _labels_code()
    result = subprocess.run(
        ["bash", "-c", f'set -euo pipefail\n{function}{labels}echo "$LABELS"'],
        capture_output=True, text=True, check=True,
        env={**os.environ, "CI_STACK_RUNNER_LABELS": "custom,labels"},
    )
    assert result.stdout.strip() == "custom,labels"


def test_runner_labels_unset_uses_this_hosts_os_release():
    function, labels = _labels_code()
    env = {k: v for k, v in os.environ.items() if k != "CI_STACK_RUNNER_LABELS"}
    result = subprocess.run(
        ["bash", "-c", f'set -euo pipefail\n{function}{labels}echo "$LABELS"'],
        capture_output=True, text=True, check=True, env=env,
    )
    assert result.stdout.strip() == _default_labels(Path("/etc/os-release"))
    assert ",ubuntu-latest," in result.stdout
