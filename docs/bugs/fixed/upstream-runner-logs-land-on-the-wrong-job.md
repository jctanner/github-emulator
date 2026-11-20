# Bug: upstream-runner logs and step states land on the wrong job

Status: fixed 2026-11-18.

## Summary

With the upstream actions/runner, a run with two jobs running at once
mixed up their logs:

- The job server looked the job up by plan. A plan is the whole run, so
  one job's console lines and step states went to the other job, and the
  first job's log answered 404.
- Log creation answered without a path. The worker's `TaskLog` requires
  one, so every step-log upload threw.
- Timeline updates were answered with a bare array. The worker expects a
  `{count, value}` collection, treated the answer as a failed update, and
  resent it again and again.

## Fix

- The job is found from the job token, not from the plan.
- Log creation answers with the log's path and a distinct id.
- Uploaded step logs are kept in their own files beside the job log, which
  the console feed already writes, so no line is doubled.
- Timeline updates are answered with `{count, value}`.

Tests: `tests/actions/test_runner_job_logs.py`.
