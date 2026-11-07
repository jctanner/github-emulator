# Bug: a git push to a pull request's branch raises no synchronize

## Summary

After a `git push` over HTTP to the head branch of an open pull request,
no `pull_request_target` `synchronize` activity was raised, so workflows that
re-run on new commits (Fullsend's review after a fix) did not run.

## Cause

`smart_http._post_receive_pack_tasks` set each open pull request's
`head_sha` to the pushed commit and committed, then called
`process_push_event`. That function skips a pull request whose recorded head
already equals the pushed commit (a guard against pull requests opened after
the push), so it skipped every one.

## Fix

Raise push activity first, then record the heads and `last_push_by_id`.
Regression test: `tests/test_update_refs_graphql.py::
test_a_git_push_to_a_pull_request_head_synchronizes_it` (fails before the
fix: no synchronize run).
