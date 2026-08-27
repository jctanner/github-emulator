# Fullsend installation tokens were attributed to `admin`

## Symptoms

GitHub App installation-token requests were resolved to the user that owned
the installation record. In the Fullsend development stack that user was
`admin`, so reviews, comments, and other events created by Fullsend appeared
to be authored by `admin`. A review emitted a `pull_request_review` event,
which re-entered the Fullsend target shim and dispatched another review run.

## Cause

The emulator had an installation owner but no distinct GitHub App bot actor.
The `ghs_` authentication paths returned `AppInstallation.user`, and the
workflow payload builder hardcoded every actor type to `User`.

## Fix

- Associate each `GitHubApp` with a stable `<app-slug>[bot]` user account.
- Resolve installation-token authentication to that bot while retaining the
  installation account as the repository/account owner.
- Preserve the bot type in webhook and Actions activity payloads.
- Have the Fullsend target shim ignore bot-authored pull-request reviews.
- Lazily create bots for existing Apps and add the SQLite compatibility column
  during startup initialization.

## Verification

- Focused emulator App tests: 9 passed.
- Live minted installation token resolved to `fullsend-triage[bot]` with
  `type: Bot`.
- A controlled live review was recorded as `fullsend-triage[bot]`; no new
  central `.fullsend` run was created by the resulting review event.
