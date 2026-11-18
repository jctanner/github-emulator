# Bug: gh in an upstream-runner step calls api.github.com

Status: fixed 2026-11-18.

## Summary

On the upstream actions/runner, every `gh` call in a step went to
api.github.com with the emulator's job token and failed with "Bad
credentials". `gh` ignores `GITHUB_API_URL`. It picks its host from
`GH_HOST`, and for any host other than github.com it reads the token from
`GH_ENTERPRISE_TOKEN`. The job message set neither. The emulator's Python
runner already set both.

## Fix

- Every step gets `GH_HOST` set to the emulator's host.
- The token the step chose (`GH_TOKEN` or `GITHUB_TOKEN`) is mirrored into
  `GH_ENTERPRISE_TOKEN` as the same expression.
- A step that named no token gets none, and a value the workflow sets
  itself wins.

Tests: `tests/actions/test_runner_gh_host.py`, and the step environment
check in `tests/actions/test_execution.py`.
