# Bug: a run's jobs list shows jobs still waiting on needs

Status: fixed 2026-11-14.

## Summary

Probed side by side with GitHub, with a workflow where jobs `b` and `c` need
job `a`. While `a` ran, GitHub's `GET /repos/{owner}/{repo}/actions/runs/{id}/jobs`
listed only `a` (`total_count` 1). `b` and `c` appeared once they were
queued.

The emulator listed `b` and `c` with status `waiting`. GitHub uses `waiting`
for a job held for an environment approval. A stage that had not started
looked like part of the run, and a client that checks once that a job is not
going to run read it as pending.

## Fix

The jobs list leaves out jobs that are still blocked on `needs:`. A job is
listed from when it is queued.

Tests: `tests/actions/test_jobs_list_unstarted.py`.
