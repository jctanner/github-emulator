# Bug: Organization profile omits imported repositories

## Summary

The web UI organization profile page (`/ui/{org}`) showed fewer repositories
than actually existed for the organization. This affected imported organization
repositories whose `full_name` used the organization namespace, but whose
`owner_id` still pointed at the importing/admin user.

## Status

Fixed. Organization profile pages now list repositories by organization
namespace (`full_name` starts with `{org}/`) and `owner_type == "Organization"`
instead of relying on `Repository.owner_id == Organization.id`.

## Impact

Medium. Repository detail pages and Git URLs could still work, but the
organization landing page under-reported the available repositories.

## Root Cause

`src/app/web/routes.py` used one repository query for both users and organizations:
`Repository.owner_id == profile.id`. Repository records are keyed to users for
database ownership, while organization imports store the display namespace in
`Repository.full_name` and set `owner_type` to `Organization`.

## Verification

- `uv run pytest tests/test_web_profiles.py tests/actions/test_web.py -v`
  passed: 8 tests.
- Added `test_org_profile_lists_repos_by_org_namespace`, which creates
  organization repositories under `opendatahub-io/*` with an importing user as
  `owner_id` and confirms `/ui/opendatahub-io` lists all org-namespaced repos.
