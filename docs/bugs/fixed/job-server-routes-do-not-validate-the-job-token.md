# Bug: job-server routes read the job token without validating it

Status: fixed 2026-11-18.

## Summary

The upstream runner calls the job-server routes with the job's own token:
timeline records and feed, job logs, action download info and the action
archive. GitHub accepts only a token it issued for that job.

The emulator decoded the token's payload and took the job id from its `sub`
claim. It did not check the signature, the issuer or the lifetime, which
`validate_job_token` in `src/app/services/job_token_service.py` already does
for the other job-token routes.

## Fix

`_job_from_token_value` in `src/app/api/actions_distributed_task.py` now
authenticates through `validate_job_token`, and answers 401 `Invalid job
token` when it returns nothing. The runner's own token is accepted as before.

Tests: `tests/actions/test_job_token_routes.py`. For every job-server route
and the action archive, a token with a bad signature, an unsigned token, a
token with another issuer and an expired token each get 401. The job's own
token is still accepted.
