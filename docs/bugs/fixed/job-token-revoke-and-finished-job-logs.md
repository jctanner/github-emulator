# Bug: a job token can't revoke itself, and a finished job's log is cut short

Status: fixed 2026-11-19.

## Summary

Compared with the same workflow run on GitHub:

- `DELETE /installation/token` with a job's `GITHUB_TOKEN` answered 403
  "Resource not accessible by personal access token". On GitHub the job token
  is an installation token, so the call answers 204 and the token is refused
  from then on. Clients that revoke their token when a step ends took the 403
  as a failure.
- `GET /repos/{owner}/{repo}/actions/jobs/{id}/logs` served the live console
  feed even after the job finished. The runner cuts each feed line at 1024
  characters, so a long line came back cut short. GitHub serves a finished
  job's log from the step logs the runner uploads, which hold each line whole.

## Fix

- A job token may call `DELETE /installation/token` whatever permissions its
  job declared, and gets 204. From then on the REST, GraphQL and git routes
  answer 401 "Bad credentials" for it, public repositories included. A
  personal access token still gets 403.
- The runner authenticates with the same token, so the revocation doesn't
  apply to the runner protocol: the job still reports its steps, logs and
  result.
- The revocation is kept in memory until the token expires: the job's
  timeout plus 5 minutes (6 h 5 min by default, 24 h at most). After that,
  token validation refuses the token anyway.
- A finished job's log is its uploaded step logs, joined in log-id order. A
  running job, or a finished one with no uploaded step logs, still gets the
  live feed.

## Known gaps

- Revocations live in the server process, so a restart forgets them.

Tests: `tests/actions/test_job_token_installation_routes.py`
(`test_a_job_token_revokes_itself_for_the_api_only`,
`test_a_revoked_job_token_is_refused_even_on_a_public_repository`,
`test_a_personal_access_token_cannot_use_the_job_token_revoke`,
`test_a_revocation_is_kept_until_its_token_expires`) and
`tests/actions/test_finished_job_logs.py`
(`test_a_finished_job_serves_its_uploaded_step_logs`,
`test_a_running_job_serves_the_live_feed`).
