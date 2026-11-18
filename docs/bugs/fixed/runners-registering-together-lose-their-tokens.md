# Bug: runners registering together lose their tokens

Status: fixed 2026-11-18.

## Summary

Runners started at the same time with `config.sh` got 401 on their next call
to the runner API. Two causes:

- **One shared row for unnamed registrations.** `config.sh` posts only `url`
  and `runner_event` to `/actions/runner-registration`; the runner's name comes
  later, with the agent add (`POST _apis/distributedtask/pools/1/agents`). The
  emulator named every such registration `unnamed-runner`. Its reuse-by-name,
  meant for a pod that restarts under the same name, then re-keyed that one row
  for each new runner, and the tokens handed to the earlier runners stopped
  working.
- **A registration token worked once.** The emulator deleted the registration
  token after one use, so a second runner using the same token got 401.

On GitHub each `runner-registration` call gives the runner its own credential,
the runner is replaced by name only at the agent add (`config.sh --replace`),
and one registration token registers any number of runners until it expires.

## Reproduction

1. Get one registration token for a repository or enterprise.
2. Run `config.sh` for three runners with different names at the same time.
3. Call `GET _apis/distributedtask/pools` with each runner's credential.

Expected: 200 for all three. Actual: the earlier runners get 401, and with a
single token the second and third registrations get 401.

## Fix

- A registration reuses an existing row by name only when the request names
  the runner. An unnamed registration always creates a new runner.
- The registration token is kept until it expires, on every route that
  takes one: `/actions/runner-registration`, the pool agent add with a
  token in the body, and the repository and enterprise pipelines register.

Tests: `tests/actions/test_unnamed_runner_registration.py`
(`test_unnamed_registrations_keep_their_own_tokens`,
`test_a_named_registration_still_reuses_its_row`,
`test_one_registration_token_registers_several_runners`,
`test_a_repository_token_registers_several_runners_on_every_route`).
