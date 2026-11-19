# Bug: pool agent routes cover every runner on the instance

Status: fixed 2026-11-18.

## Summary

On GitHub a self-hosted runner belongs to the repository, organization or
enterprise it registered with. Its credential lists, updates and removes only
the agents in that scope.

The emulator's pool agent routes (`GET`, `PUT`/`PATCH` and `DELETE`
`/_apis/distributedtask/pools/{pool}/agents...`) matched every runner on the
instance. Listing by `agentName` could return a runner of another repository
with the same name, and `config.sh --replace` could then update it.

## Fix

`_same_scope_as` in `src/app/api/actions_distributed_task.py` matches runners
with the caller's `repo_id`, `org_id` and `enterprise_slug`.
`dt_list_pool_agents`, `dt_update_pool_agent` and `dt_delete_pool_agent` use
it. A runner outside the caller's scope is not listed, and updating or
removing it is 404.

Tests: `tests/actions/test_runner_agent_scope.py`.
