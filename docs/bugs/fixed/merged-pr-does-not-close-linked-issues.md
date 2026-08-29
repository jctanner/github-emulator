# Merged pull request does not close linked issues

## Symptom

A pull request containing `Closes #5` merged successfully, but issue #5 stayed
open and GraphQL `closingIssuesReferences` returned an empty connection.

## Cause

Every merge path closed only the issue row that represents the pull request.
The emulator did not parse or resolve GitHub closing keywords in pull-request
descriptions or commit messages.

## Fix

The emulator now supports GitHub's nine case-insensitive closing keywords,
optional colons, same- and cross-repository references, and the default-branch
restriction. REST, GraphQL, web, and queued auto-merge paths close linked
issues, maintain repository counters, identify the closing actor, and emit an
`issues: closed` event. GraphQL exposes description-linked issues through
`closingIssuesReferences`; commit-message references apply only at merge time.

## Regression coverage

`tests/test_closing_issues.py` covers keyword syntax, commit-message behavior,
GraphQL linkage, automatic closure, non-default branches, and cross-repository
references.
