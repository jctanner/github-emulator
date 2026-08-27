# Pull-request web labels

## Goal

Show labels attached to pull requests in the emulator’s pull-request list and
detail views.

## Implementation

- Reuse the labels on the pull request’s underlying issue record.
- Render the labels with the existing `IssueLabel` color styling.

## Verification

- Added coverage for both list and detail pages.
- Pull-request and issue web suites pass: 28 tests.
