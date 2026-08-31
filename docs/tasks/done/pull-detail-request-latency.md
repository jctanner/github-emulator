# Pull-detail request latency

## Problem

Loading one pull request took roughly 0.4–0.8 seconds anonymously and could
take several seconds in the browser. Firefox requests carried both the signed
UI cookie and cached Basic credentials, causing parallel API reads to perform
synchronous bcrypt checks. The pull serializer also permitted automatic ORM
relationship loading, producing unnecessary serialized SQLite queries.

## Changes

- Use joined loading for scalar repository and pull-request relationships.
- Reject unspecified pull-request relationship loads and explicitly load only
  the issue labels required by workflow event payloads.
- Run Basic password verification outside the asyncio event loop.
- Coalesce and briefly cache successful password checks using a key bound to
  the username, supplied password, and current stored password hash.
- Route PAT-shaped Basic credentials directly to token validation.
- Prefer a valid signed UI session for same-origin browser requests when a
  browser also supplies a cached Authorization header.

## Validation

- Focused authentication, browser-session, and pull-request suite: 31 passed.
- Full backend suite before the final relationship guard and browser-session
  preference: 324 passed; the affected focused suites passed again afterward.
- Deployed anonymous pull-detail requests improved from approximately
  0.4–0.8 seconds to approximately 0.13–0.19 seconds; a Playwright page load
  measured the PR request at 319 ms while its sibling APIs completed in
  34–70 ms.
- A cold Basic password request retains the intentional bcrypt cost; subsequent
  requests during the 30-second cache window match anonymous latency.
