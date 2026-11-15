# CC-43: Releases and container images

## Status

Current: workflow implementation prepared; merge and publication require green CI.

## Contract

Human approved UTC CalVer YYYY.MM.DD[.N] on 2026-11-15. Existing release
schemes take precedence. Remote tags/releases and versioning documentation
were checked before implementation. No established remote release scheme was found.
Package metadata versions remain unchanged. Default-branch images get latest
and sha-<full SHA>; immutable releases get the exact CalVer tag. PRs never
publish. The workflow publishes the smoke-tested local image, then creates
a GitHub release with its digest. Based on the Jira CC-43 reference pattern.

## Validation

- Workflow actionlint, publication regression tests, bash syntax and git diff --check passed locally.
- CI and container smoke: must pass in the PR before merge.
- First release and image digest: pending.
