# Bug: concurrent creates of one repository name return 500

Status: fixed 2026-11-15.

## Summary

Repository creation checks that the name is free and then inserts. Two
concurrent requests for the same name can both pass the check. The unique
index on `repositories.full_name` then refuses the second insert, and the
emulator answered that request with a 500 (`IntegrityError: UNIQUE
constraint failed`). GitHub answers a duplicate name with 422.

Behaviour-test logs showed this in every run: one to three 500s per run
from `POST /orgs/{org}/repos`, as parallel scenarios set up the same test
repositories.

## Fix

`repo_service.create_repo`, used by the organisation route, and the
`POST /user/repos` route now roll back on that IntegrityError. They answer
422 "already exists", the same as a sequential duplicate.

Test: `tests/test_repo_create_name_race.py`. It fails without the fix.
