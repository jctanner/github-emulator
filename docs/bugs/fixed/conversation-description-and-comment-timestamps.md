# Bug: Conversation descriptions and comments omit timestamps

## Summary

Issue and pull-request descriptions and comments showed author names and body
text without the creation time displayed by GitHub.

## Reproduction

1. Open an issue or pull request conversation.
2. Inspect the description and any comment headers.

## Expected

Show each description's and comment's creation timestamp, with a machine-readable
`datetime` value.

## Actual

The emulator showed no timestamp for descriptions or comments. Label events did
already show their timestamps.

## Fix

- Render issue and pull-request description creation times in their headers.
- Render comment creation times in the shared issue/pull-request timeline.
- Keep the complete localized timestamp available as visible text and a `title`.

## Evidence

- Playwright compared `https://github.com/jctanner/issuetests/issues/58` with
  `http://github.local/ui/admin/ansible-agent-harness/issues/14`.
- GitHub's page included `relative-time` nodes for its description and comments;
  the emulator rendered neither timestamps nor `time` elements.
- The emulator now renders `<time datetime>` elements for descriptions and
  comments in both issue and pull-request views.
