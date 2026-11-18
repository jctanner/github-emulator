# Bug: a pull request's author can approve it or request changes on it

Status: fixed 2026-11-14.

## Summary

GitHub refuses an `APPROVE` or `REQUEST_CHANGES` review from the pull
request's author on `POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews`:

```json
{"message": "Unprocessable Entity", "errors": ["Review Can not request changes on your own pull request"]}
```

For `APPROVE` the error text is `Review Can not approve your own pull
request`. A `COMMENT` review by the author is allowed. The same rule applies
when the author submits a pending review with
`POST .../reviews/{review_id}/events`.

The emulator accepted both events from the author with 201. A client's guard
against reviewing its own pull request was therefore never exercised.

Two smaller differences on the same endpoints:

- GitHub documents 200 for a created review; the emulator answered 201.
- GitHub submits a pending review with `POST .../events`; the emulator
  only routed `PUT`.

## Fix

`src/app/api/reviews.py`:

- `create_review` and `submit_review` refuse `APPROVE` and `REQUEST_CHANGES`
  from the pull request's author with GitHub's 422 body. A refused pending
  review stays pending.
- `create_review` answers 200.
- `submit_review` is also routed on `POST`. `PUT` stays, so existing clients
  keep working.

The emulator has no GraphQL `addPullRequestReview` or
`submitPullRequestReview` mutation, so there is no GraphQL path to change.

No existing test pinned 201 for a reason GitHub documents. Tests that pinned
201 now expect 200. Tests in which the author approved or requested changes
on their own pull request now have a second user submit the review. What
they check (the event state, the review's comments, the triggered workflow
event) is unchanged.

Tests: `tests/test_review_own_pull_request.py`.
