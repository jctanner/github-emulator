# Bug: an update of a row another request deleted answers 500

Status: fixed 2026-11-15.

## Summary

A test deleted its repository while a runner was still reporting the end of
a job there. Deleting a repository purges its workflow runs and jobs. The
runner's plan event had already loaded the job, so its UPDATE matched no
row and SQLAlchemy raised `StaleDataError`, which the emulator answered
with 500.

## Fix

`StaleDataError` now answers 404 "Not Found", logged at info level on the
`github_emulator` logger: the resource is gone.

## Evidence

- An agents behaviour-test run on the emulator, emulator log:
  `DELETE /api/v3/repos/halfsend/test-repo-02` 204, then the job's
  timeline and event calls 401, then `POST .../plans/3f7993f7-.../events`
  500 with `StaleDataError: UPDATE statement on table 'workflow_jobs'
  expected to update 1 row(s); 0 were matched.`
