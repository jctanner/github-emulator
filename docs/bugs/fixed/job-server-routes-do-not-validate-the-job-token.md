# Bug: job-server routes read the job token without validating it

Status: fixed 2026-11-18.

## Summary

The upstream runner calls the job-server routes with the job's own token:
timeline records and feed, job logs, action download info and the action
archive. It addresses each call with the plan (the run) and timeline (the
job) ids from its job message. GitHub accepts only a token it issued for that
job, and only for that job's plan and timeline.

The emulator decoded the token's payload and took the job id from its `sub`
claim. It did not check the signature, the issuer or the lifetime, which
`validate_job_token` in `src/app/services/job_token_service.py` already does
for the other job-token routes. The hub routes then chose the job to write to
from the plan or timeline id in the path, and used the token's job only when
that lookup found nothing.

## Fix

- `_job_from_token_value` in `src/app/api/actions_distributed_task.py` now
  authenticates through `validate_job_token`, and answers 401 `Invalid job
  token` when it returns nothing. The runner's own token is accepted as
  before.
- `validate_job_token` refuses a token whose header or payload is not a JSON
  object, instead of failing with a server error.
- `_job_for_plan` checks that the plan and timeline in the path are the
  token's own, and answers 404 `Plan not found` or `Timeline not found`
  otherwise. The feed, timeline records, log and action download info routes
  write only to the token's job.

Tests:

- `tests/actions/test_job_token_routes.py`: for every job-server route and the
  action archive, a token with a bad signature, an unsigned token, a token
  with another issuer, an expired token and tokens whose header or payload is
  not an object each get 401. The job's own token is still accepted.
- `tests/actions/test_job_token_route_targets.py`: a job's token gets 404 on
  another run's plan and on another job's timeline in the same run, and the
  other job's log stays empty.
