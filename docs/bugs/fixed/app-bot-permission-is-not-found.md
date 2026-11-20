# Bug: an App's bot has no collaborator permission

Status: fixed 2026-11-14.

## Summary

`GET /repos/{owner}/{repo}/collaborators/{bot}/permission` returned 404
for an App's bot (`<slug>[bot]`) without a collaborator row. GitHub
returns 200 with `"permission": "none"`, an empty `role_name` and every
permission flag false. It does so on a public repository, where a person
without access gets `read`, and when the App is installed on the
repository with write access.

Checked on github.com: three bots of Apps installed on a public
repository, and an uninstalled App's bot on a private repository.

A client saw an API error where GitHub gives a clean "no permission".

## Fix

The endpoint answers `none` for a bot without a collaborator row. It still
does not report what an installation grants; git push and contents writes
keep following the installation.

Tests: `tests/test_app_installation_grants.py`,
`tests/test_installation_administration.py`.
