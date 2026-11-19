# Bug: the Python runner runs bash steps with /bin/bash

Status: fixed 2026-11-18.

## Summary

The emulator's Python runner always ran `shell: bash` steps with
`/bin/bash`. On macOS that is bash 3.2, which rejects bash 4 syntax such as
`;;&` and `${var,,}` that steps on GitHub-hosted runners use. actions/runner
runs the `bash` it finds on `PATH`.

## Fix

The Python runner resolves `bash` from the step's `PATH`, as actions/runner
does, and falls back to `/bin/bash`.

Tests: none new. The change is one lookup, covered by the existing step
execution tests on a host whose `PATH` bash is `/bin/bash`.
