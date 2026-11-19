# Bug: a repository's private flag and visibility drift apart

Status: fixed 2026-11-14.

## Summary

GitHub treats `private` and `visibility` as one setting. The emulator's
`PATCH /repos/{owner}/{repo}` set only the field it was given:

- `{"private": false}` left `visibility: "private"`.
- After `{"private": true}`, `{"visibility": "public"}` left `private: true`.

A client that read `visibility` saw the wrong value.

## Fix

The update route moves the other field too. Visibility wins when both are
sent, and `internal` counts as private, as on GitHub.

Tests: `tests/test_repo_visibility_sync.py`.
