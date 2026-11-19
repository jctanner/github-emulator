# Bug: two runners can claim the same queued job

Status: fixed 2026-11-14.

## Summary

On GitHub, each job is assigned to exactly one runner and runs once.

The emulator sometimes ran a job on two runners. It was reproduced on both
runner protocols:

- **Emulator runner.** Four site-wide emulator runners and eight concurrent
  workflow runs (32 jobs): five jobs were each executed by two runners. Both
  runners' logs had `=== Job #N finished` for the same N.
- **Upstream runner.** Two `actions/runner` 2.338.2 runners at enterprise
  scope both logged `Running job` for job request 1. The emulator recorded
  only the second one as the job's runner.

Both claim paths read the queued jobs and then wrote the assignment without
a condition:

- `_poll_for_jobs` in `src/app/api/actions_dispatch.py`, used by
  `GET /repos/{owner}/{repo}/actions/runner/jobs` and the site-wide
  `GET /actions/runner/jobs`.
- `_claim_next_job` in `src/app/api/actions_distributed_task.py`, used by the
  upstream runner's message poll.

Each poll has its own database session. Two polls that both read a job as
`queued` before either wrote it both set it `in_progress` with their own
runner and both returned it. The second write replaced the first runner's
name, and both runners were marked busy.

The upstream runner that lost the job got a bare 404 on every lock renewal
(`PATCH .../_apis/distributedtask/pools/1/jobrequests/1?lockToken=...`). It
logged "Retrying lock renewal" and never took another job, so two runners
worked like one.

The upstream runner (`JobDispatcher.RenewJobRequestAsync`) stops renewing
only on `TaskAgentJobNotFoundException` or `TaskAgentJobTokenExpiredException`.
Its HTTP client reads the exception type from the error body's
`typeKey`/`typeName`, and a bare 404 becomes a generic error that is retried.
When the first renewal stops that way, the runner abandons the job and goes
back to polling.

## Fix

Both paths now claim through `_claim_queued_job`, a conditional update:

```sql
UPDATE workflow_jobs SET status='in_progress', runner_id=..., runner_name=..., started_at=...
WHERE id=:id AND status='queued'
```

Only the poll whose update changes one row owns the job, and only that
poll marks its runner busy. A poll that loses the race moves on to the next
matching job, or returns 204 when there is none.

Accepting, renewing (`PATCH`) or completing (`DELETE`) a job request that the
runner does not hold now returns a 404 with a `TaskAgentJobNotFoundException`
body, so the runner gives the job up instead of retrying.

Tests: `tests/actions/test_job_claim_race.py`.

- Two polls run against one queued job, once for each claim path. A hook
  holds both polls after their read until the first has claimed, so the
  interleaving is forced rather than left to timing. Exactly one poll
  receives the job, and the other runner stays idle.
- A lock renewal from a runner that does not hold the job request gets the
  `TaskAgentJobNotFoundException` body.

## Not changed

An empty `_poll_for_jobs` sleeps 2 s before it reads again, so a job queued
by a finished `needs:` dependency waits up to 2 s for a polling runner. A
wake-up signal would need to work across the request sessions and any
worker processes, so it is left out of this fix.
