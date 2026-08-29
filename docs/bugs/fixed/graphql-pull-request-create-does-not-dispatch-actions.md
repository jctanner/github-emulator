# GraphQL pull-request creation does not dispatch Actions

## Symptom

Pull requests created by `gh pr create` appeared in the emulator, but workflows
listening for `pull_request_target: opened` did not run. Pull requests created
through the REST API did run the same workflows.

## Cause

The REST pull-request endpoint dispatched the activity event, while the
GraphQL `createPullRequest` mutation only persisted the issue and pull-request
records. GitHub CLI uses the GraphQL mutation.

## Fix

The GraphQL mutation now emits the same `pull_request_target: opened` payload
as the REST path after the pull request is committed.

## Regression coverage

`tests/test_actions_event_triggers.py` covers workflow dispatch from GraphQL
pull-request creation.
