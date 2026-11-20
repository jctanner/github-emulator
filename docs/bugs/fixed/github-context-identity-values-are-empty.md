# Bug: the github context's identity values are empty

Status: fixed 2026-11-18.

## Summary

Compared with the same workflow run on GitHub, these `github` context values
were empty in expressions the emulator rendered (a step's `env:`, `run:` and
`with:`): `actor`, `actor_id`, `triggering_actor`, `api_url`, `graphql_url`,
`repository_id`, `repository_owner_id`, `run_attempt` and `workflow_ref`.

The job message's github context, which the runner exports as `GITHUB_*`
variables, lacked `actor`, `actor_id`, `triggering_actor` and `workflow_ref`, so
`GITHUB_ACTOR`, `GITHUB_ACTOR_ID`, `GITHUB_TRIGGERING_ACTOR` and
`GITHUB_WORKFLOW_REF` were unset in every step. The other five were already
there.

The REST run and job objects also lacked fields GitHub fills: runs had no
`run_started_at` or `display_title`, and jobs had no `created_at`, `head_sha`
or `head_branch`.

## Fix

- Server-side rendering and the job message's github context carry the nine
  values. Ids and `run_attempt` are strings, as GitHub gives them.
- Runs report `run_started_at` and `display_title`: the head commit's first
  line (a push payload now carries `head_commit`), the pull request's or issue's title, or the workflow name.
- Jobs report `created_at`, `head_sha` and `head_branch`.
- A re-run renders its own `github.run_attempt`: the attempt is set before the
  steps render, and the re-run's jobs report it too.

## Known gaps

- A re-run is a new run whose actor is the user who asked for it, so
  `actor` and `triggering_actor` are both that user. On GitHub `actor` stays
  the user who started the first attempt.
- `display_title` does not use the workflow's `run-name:`.

Tests: `tests/actions/test_github_identity_context.py`
(`test_step_env_renders_the_identity_values`,
`test_the_job_message_carries_them_for_the_runner`,
`test_run_and_job_fields_github_fills`,
`test_a_rerun_renders_its_own_attempt`,
`test_a_push_run_is_titled_by_its_commit_and_knows_branch_protection`).
