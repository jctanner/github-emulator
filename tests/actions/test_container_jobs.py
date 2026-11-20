"""Jobs that need containers fail loudly instead of running on the host.

Neither the Python runner nor the upstream-runner path starts a job container
or service containers. Running such a job's steps anyway put them on the
runner host, where a check like "is this Alpine?" failed and a check that
only needed a shell passed: a result about the host, not the workflow.
"""

import subprocess

import pytest

from app.services.workflow_service import build_job_graph


def _job(config: dict) -> dict:
    [job] = build_job_graph({"on": "push", "jobs": {"j": config}})
    return job


@pytest.mark.parametrize("config, named", [
    ({"container": "alpine:3.20"}, "container:"),
    ({"container": {"image": "alpine:3.20"}}, "container:"),
    ({"services": {"db": {"image": "postgres:16"}}}, "services:"),
    ({"container": "alpine:3.20", "services": {"db": {"image": "postgres:16"}}},
     "container: and services:"),
])
def test_a_container_job_is_refused_not_run_on_the_host(config, named):
    job = _job({"runs-on": "ubuntu-latest", **config, "steps": [{"run": "echo would-run"}]})
    [step] = job["steps"]
    assert "would-run" not in step["run"]
    assert f"::error::{named} not supported" in step["run"]
    # The refusal really fails, and says why on stderr.
    result = subprocess.run(["bash", "-c", step["run"]], capture_output=True, text=True)
    assert result.returncode == 1
    assert "were NOT run" in result.stderr


def test_a_job_without_containers_keeps_its_steps():
    job = _job({"runs-on": "ubuntu-latest", "steps": [{"run": "echo would-run"}]})
    assert job["steps"] == [{"run": "echo would-run"}]


def test_an_empty_container_key_is_not_a_container():
    job = _job({"runs-on": "ubuntu-latest", "container": "", "steps": [{"run": "echo ok"}]})
    assert job["steps"] == [{"run": "echo ok"}]
