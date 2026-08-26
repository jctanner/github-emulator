# Recover jobs from stale runners

## Goal

Allow a replacement Actions runner to claim a job whose original runner
disappeared after assignment.

## Failure observed

Job 507 remained `in_progress` with runner #35 after the runner pod was
restarted. The replacement pod registered as runner #36, but polling only
considered `queued` jobs, so #507 never ran and had no live log stream.

## Implementation

- Added `GITHUB_EMULATOR_RUNNER_STALE_THRESHOLD_SECONDS`, defaulting to 120.
- Before polling, jobs assigned to runners whose heartbeat is older than the
  threshold are returned to `queued`.
- The stale runner is marked offline and the job's runner assignment, start
  time, and step results are reset.
- Added an execution-contract regression test covering reassignment to a
  replacement runner.

## Verification

- `19 passed` across the Actions API, execution, web, and live-log tests.
- `make host-rebuild-github` completed successfully.
- Job 507 was observed changing from `in_progress`/runner #35 to
  `queued`/unassigned after deployment.
