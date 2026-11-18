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
- An enterprise remove token serves `config.sh remove` only: a
  `runner-registration` call with `runner_event` `remove`. It registers no
  runner on any route (migration `0018_enterprise_remove_tokens` adds
  `for_remove` to tell it apart).
- Issuing a registration or remove token drops the tokens past their expiry.
- `config.sh remove` deletes the target agent with the credential from its own
  unnamed `runner-registration` call; that broker row is deleted with the agent,
  as `--replace` already did, so removals leave no `unnamed-runner` rows.

Tests: `tests/actions/test_unnamed_runner_registration.py`
(`test_unnamed_registrations_keep_their_own_tokens`,
`test_a_named_registration_still_reuses_its_row`,
`test_one_registration_token_registers_several_runners`,
`test_a_repository_token_registers_several_runners_on_every_route`,
`test_a_remove_token_serves_remove_and_registers_no_runner`,
`test_config_sh_remove_leaves_no_runner_behind`,
`test_expired_registration_tokens_are_pruned`).
