# Issue cross-references

## Goal

When an issue or pull request mentions another issue, the mentioned issue's
history links back to it, as GitHub's "mentioned this issue" entries do.

## Implementation

- `app.services.cross_references` hooks the SQLAlchemy `Session`, like
  `live_updates`, so every writer is covered: REST, GraphQL, the web UI and
  Actions.
  - `before_flush` notes each new issue, pull request or issue comment, and
    each edited body.
  - `after_flush_postexec` resolves the mentions, once ids exist, and stages a
    `cross-referenced` `IssueEvent` on each mentioned issue. The commit's
    next flush writes it, and `live_updates` then refreshes an open page of
    the mentioned issue.
- **What counts as a mention:** `#12`, `owner/repo#12`, and this emulator's
  own `/{owner}/{repo}/issues/12` or `/pull/12` URLs (with or without
  `/ui/`). Mentions in fenced or inline code are ignored, as are a bare
  `a#12`, `&#12;`, other hosts, missing issues and an issue mentioning itself.
- One event per source and target, however often the mention repeats in
  comments or edits. The actor is the author of the text.
- Migration `0012_issue_event_source` adds `issue_events.source_issue_id`.
  Repository purge treats it as a pointer, so deleting the source's
  repository clears it and the timeline skips the event.
- `GET /repos/{owner}/{repo}/issues/{n}/timeline` (the `timeline_url` issues
  already advertised) lists comments as `commented`, label events, and
  `cross-referenced` entries with `source: {type: "issue", issue: {...}}`.
  As on GitHub, cross-references are not in `/events`, have no id, and are
  hidden when the viewer cannot read the source's repository.
- The issue and pull-request pages read the timeline and show "*actor*
  mentioned this issue" (or "pull request") with the source's state icon,
  title and `owner/repo#n`, linked to it.

Not done: GraphQL `timelineItems`, the "will close this issue" link and
**Development** sidebar for closing keywords, "closed this in #n" on merge,
and "referenced" events from commit messages. Mentions in review summaries
and review comments are not read.

## Evidence

- `tests/test_cross_references.py` (7 tests): mention parsing, a pull request
  referencing an issue, once per source across comments and edits, an edit
  adding a mention, self and code mentions, cross-repository visibility, and
  timeline order. Backend: 765 passed.
- Frontend: a cross-reference test in `IssueComments.test.tsx`; 78 tests,
  lint, typecheck and Prettier clean.
- Deployed; the live database migrated to `0012_issue_event_source` with its
  existing events intact (backup: `/data/pre-cross-references.db.bak`). In
  headless Chrome, creating `admin/review-ui-check#3` with "Related to #2 and
  the work in #1." showed the entry on the open issue #2 page without a
  reload, and on pull request #1 as "mentioned this pull request".
