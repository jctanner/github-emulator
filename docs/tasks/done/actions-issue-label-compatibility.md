# Actions issue-label compatibility

## Goal

Make Fullsend's GitHub CLI label mutations behave like GitHub against the
emulator.

## Failure observed

Job 587 reported that it applied `documentation` and `ready-to-code`, but the
issue had no labels. The repository already contained `ready-to-code`, so its
duplicate `gh label create` response of HTTP 422 was expected. The actual
problem was that issue-label POST handling did not support the CLI's
`labels[]` form payload and silently ignored labels that were not already in
the repository.

## Implementation

- Accept JSON `labels` arrays and URL-encoded `labels[]` form fields.
- Create missing repository labels with GitHub's default `ededed` color before
  attaching them to an issue.
- Apply the same parsing/creation behavior to replacement label updates.
- Added regression coverage for the CLI form payload and missing labels.

## Verification

- 34 focused emulator tests passed.
- `documentation` and `ready-to-code` are now both created and attached by the
  form-style issue-label request in the regression test.
