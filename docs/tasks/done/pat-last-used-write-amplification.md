# PAT last-used write amplification

## Goal

Prevent token-authenticated browser page loads from turning every parallel API
read into a competing SQLite write solely to update PAT usage metadata.

## Scope

- Throttle `last_used_at` updates while preserving useful recent-use metadata.
- Serialize the infrequent update per token and end stale read transactions
  before waiting for the update lock.
- Add regression coverage for consecutive and concurrent PAT-authenticated reads.
- Compare anonymous, cookie-authenticated, and PAT-authenticated request timing.

## Outcome

- PAT usage timestamps are updated at most once per five-minute interval.
- Concurrent stale-token requests serialize the one required timestamp update;
  subsequent requests remain read-only.
- The read transaction is rolled back before waiting for the per-token lock,
  avoiding SQLite lock contention from parallel browser API requests.
- The complete backend suite passes with 323 tests.
- The rebuilt deployment serves the commit API and UI route in about 40 ms for
  anonymous validation requests; authenticated browser timing remains the
  final environment-specific confirmation.
