# Bug: a failed post-push step drops the push event

Status: fixed 2026-11-16.

## Summary

After a push, the emulator runs several steps: sync the branches, index the
repository, raise the push event, and update pull request heads. Each step
swallowed its error, with no rollback and no log. A failed statement leaves
the session unusable until it is rolled back (PostgreSQL aborts the whole
transaction), so every later step failed too. The push then never raised its
pull request's `synchronize` event or started the workflows that listen for
it, and nothing in the log said why.

## Fix

`_post_receive_pack_tasks` in `src/app/git/smart_http.py` logs each step's
error, rolls back, and reloads the repository and user before the next step.

## Evidence

- `tests/test_post_push_step_failure.py`: the indexing step is made to fail,
  and the push event still runs. It fails on the previous code.
- Found by a behaviour run on PostgreSQL, where the review workflow was never
  dispatched after a push because indexing had failed first.
