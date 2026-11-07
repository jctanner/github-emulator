# Bug: creating, moving, or deleting a tag raises no events

## Summary

`process_push_event` handles branches only. A tag created, moved, or deleted
by `git push`, REST `git/refs`, or GraphQL `updateRefs` raises no `push`
webhook or Actions run (`on: push: tags:`), and no `create`/`delete` webhook
with `ref_type: "tag"`.

## Expected

As on GitHub: a push event with `ref: refs/tags/<name>`, plus `create` or
`delete` with `ref_type: "tag"`; no pull request synchronization.
