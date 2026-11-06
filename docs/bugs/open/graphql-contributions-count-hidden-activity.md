# Bug: `contributionsCollection` counts activity in hidden repositories

## Summary

`user.contributionsCollection` (`src/app/graphql/types/contributions.py`)
counts commits, issues, pull requests, and reviews in every repository, so
anyone can read another user's daily activity in private repositories they
cannot see. GitHub counts private contributions only for the user, or shows
them anonymised when the user opts in.

## Impact

Activity timing in private repositories is visible to anyone through the
calendar. No repository names or content leak.
