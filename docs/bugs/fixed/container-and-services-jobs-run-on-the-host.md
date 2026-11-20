# Bug: container and services jobs run their steps on the runner host

Status: fixed 2026-11-14.

## Summary

On GitHub, a job with `container:` runs its steps inside that container, and
`services:` starts service containers beside the job. Neither runner path in
the emulator starts job or service containers. A job with either key ran its
steps on the runner host instead:

- a step that checked the container (`grep Alpine /etc/os-release`) failed;
- a step that needed only a shell passed.

Either way the outcome described the runner host, not the workflow.

## Fix

`workflow_service.build_job_graph` replaces the steps of a job with
`container:` or `services:` with one step that fails with:

`::error::container: not supported by this emulator's runners; the job's steps were NOT run`

The message names whichever keys are present. This follows the existing
refusal for `docker://` actions (`_unsupported_uses_script`). An empty
`container:` is not a container, and a job without either key is unchanged.

Tests: `tests/actions/test_container_jobs.py`.
