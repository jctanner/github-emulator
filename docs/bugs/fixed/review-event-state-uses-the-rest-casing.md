# Bug: the `pull_request_review` event carries the REST API's state

Status: fixed 2026-11-08.

## Summary

GitHub reports a review's state two ways:

- the REST API says `CHANGES_REQUESTED`, `APPROVED` and `COMMENTED`;
- the `pull_request_review` event says `changes_requested`, `approved` and
  `commented`.

The emulator put the stored REST form into the event payload. A workflow
comparing `github.event.review.state` with the event's form never matched.
`tests/actions/test_event_triggers.py` asserted the REST form, which
encoded the mistake.

Fullsend starts its fix stage from the registered review bot's
`changes_requested` review. In Breadboard's behaviour-test T2 tier,
`fix-authorization.feature` ("only the registered review bot's
changes-requested review starts fix") failed with:

```
"fix" agent was not dispatched
```

The review bot had submitted its review.

## Fix

The event payload's `review.state` is lowercase. REST responses and
GraphQL still report the uppercase form.

Tests:

- `tests/test_webhook_delivery.py`
  (`test_a_review_event_reports_its_state_in_lowercase`);
- `tests/actions/test_event_triggers.py`, corrected.
