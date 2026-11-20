# Bug: the runner token exchange and pipelines deregistration ignore the runner's credential

Status: fixed 2026-11-19. Issue #43, part of #39, found in the review of #29.

## Summary

The upstream actions/runner registers an RSA public key
(`TaskAgent.authorization.publicKey`), and on
`POST /_apis/oauth2/token?runner_id=<id>` it proves itself with a JWT client
assertion signed by the private half. The emulator never stored the key and
issued a runner token after checking only `grant_type` and that the runner
existed. Anyone who knew or guessed a runner id could take the runner's
token, which also replaced the token the real runner held.

`DELETE /_services/pipelines/{owner}/{repo}/_apis/pipelines/runs/{id}` and
its enterprise counterpart took no credential at all.

## Fix

- `runners.public_key` (migration `0020_runner_public_key`) stores the key
  as PEM. Agent registration via `POST .../pools/{pool}/agents`, either with
  a broker token or a registration token, stores it. So do `PUT`/`PATCH`
  of the agent (`config.sh --replace` takes the new key) and the pipelines
  registration routes.
- `app.services.runner_credentials.verify_client_assertion` checks the RS256
  signature against that key, `iss` and `sub` against the runner's client
  id, `aud` against the authorization URL handed out at registration (or
  the URL the request was sent to), and `exp`/`nbf`, with 60 seconds of
  clock skew. The exchange answers 400
  `{"error": "invalid_client", ...}` when any check fails.
- `PUT`/`PATCH` of an agent is allowed for the runner itself, or for the
  `config.sh --replace` broker identity. The broker is the identity created
  by `POST /actions/runner-registration` (`runners.registration_broker`,
  migration `0021_runner_registration_broker`) and only counts for a target
  in the same repository, organization and enterprise scope. It stops being
  a broker once it registers as the agent or exchanges a token. Two ordinary
  runners that share a repository or enterprise cannot replace each other's
  key.
- Assertion times that are huge integers no longer escape as a 500: they are
  finite NumericDates and are compared as integers.
- The pipelines deregister routes require the runner's own token: none is
  401, another runner's is 403.

## Runners without a key

A runner with no stored key is refused on the exchange. That covers rows
registered before this fix and runners from paths that never send one. Such
a runner must run `config.sh` again. Custom-protocol runners
(`/actions/runner/register`, `/admin/actions/runners/register`) never use
the exchange, so they are unaffected.

## Evidence

- `tests/actions/test_runner_credentials.py`: a valid assertion is
  exchanged, then used for the session, the message long-poll and the
  agent deletion `config.sh remove` makes. These are refused: a wrong key,
  an expired assertion, another runner's id, another runner's key, the
  wrong audience, no assertion, garbage, and a runner without a key.
  Replacing a runner takes the new key; another ordinary runner (other or
  same repository) and a broker from another repository are refused with
  403. The deregister routes, repository
  and enterprise, accept only the runner's own token.
- `tests/test_database_migrations.py`: 0018 adds the column to a database
  at 0017 and keeps existing runners.
