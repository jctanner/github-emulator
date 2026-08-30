# Task: Repository Owner Selector

## Goal

Allow the public `/ui/new` page to create a repository under either the
signed-in user's account or an organization where that user is an active
member.

## Acceptance Criteria

- [x] The form displays the personal account and eligible organizations.
- [x] The selected namespace is preserved when validation fails.
- [x] Repository creation redirects to the selected owner namespace.
- [x] A forged or unavailable organization selection is rejected.
- [x] The organization repository API enforces the same membership boundary.
- [x] Personal-account creation remains backward compatible when owner is omitted.
- [x] Tests cover rendering, organization creation, and authorization failure.

## Status

Complete

## Notes

- The emulator does not yet model GitHub organization repository-creation
  policies, so every active organization member is considered eligible.
- Focused web, organization, and repository suites pass: 23 tests.
