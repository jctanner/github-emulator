# Bug: git push fails for large repositories via Smart HTTP

## Summary

Pushing large repositories to the GitHub emulator via git Smart HTTP (`git-receive-pack`) fails with 401 Unauthorized on the `GET /info/refs?service=git-receive-pack` request, followed by a 502 from the reverse proxy. Small repositories (e.g., rfe-creator, strat-creator) push successfully using the same credentials and method.

## Status

Fixed. The first 401 is the normal Git Smart HTTP authentication challenge; the
actual large-push failure was the blocking HTTP `git-receive-pack` path. It
buffered the full pack request in memory and then ran branch sync, search
indexing, and workflow detection before returning the Git response, which made
large mirror pushes vulnerable to proxy timeouts.

The HTTP receive-pack handler now spools the request body to a temporary file,
feeds that file to `git-receive-pack`, returns the Git result immediately, and
runs post-push side effects asynchronously in a fresh DB session.

## Steps to Reproduce

1. Create a user and token on the emulator:
   ```bash
   curl -X POST http://github.local/api/v3/admin/users \
     -H "Content-Type: application/json" \
     -d '{"login": "opendatahub-io", "email": "odh@example.com"}'

   curl -X POST http://github.local/api/v3/admin/tokens \
     -H "Content-Type: application/json" \
     -d '{"login": "opendatahub-io", "scopes": ["repo"]}'
   # Returns token: ghp_<TOKEN>
   ```

2. Create a repo:
   ```bash
   curl -X POST http://github.local/api/v3/user/repos \
     -H "Authorization: token ghp_<TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"name": "architecture-context", "auto_init": true}'
   ```

3. Clone a large upstream repo locally and push:
   ```bash
   git clone https://github.com/opendatahub-io/odh-dashboard /tmp/odh-dashboard
   cd /tmp/odh-dashboard
   git remote add emulator http://x-access-token:ghp_<TOKEN>@github.local/opendatahub-io/odh-dashboard.git
   git push emulator --mirror
   ```

4. Observe: push fails with 401/502. The same token and URL pattern works for small repos like rfe-creator (~5 MB).

## Affected Repos (all failed)

- `opendatahub-io/architecture-context` — upstream: https://github.com/opendatahub-io/architecture-context
- `opendatahub-io/opendatahub-operator` — upstream: https://github.com/opendatahub-io/opendatahub-operator
- `opendatahub-io/odh-dashboard` — upstream: https://github.com/opendatahub-io/odh-dashboard

## Working Repos (same method succeeded)

- `opendatahub-io/rfe-creator` — small repo, push worked fine
- `opendatahub-io/strat-creator` — small repo, push worked fine

## Debug Details

- Token authenticates correctly via API (`GET /api/v3/user` returns opendatahub-io, id 3)
- Repos exist and are owned by opendatahub-io (owner_id 3)
- Auth handler at `src/app/api/deps.py:get_current_user` correctly parses Basic auth (`x-access-token:TOKEN`)
- Write access check at `src/app/git/smart_http.py:67-79` should pass (user.id == repository.owner_id)
- The 401 occurs on `GET /info/refs?service=git-receive-pack`, before any pack data is sent
- After the 401, git retries and the proxy returns 502

## Suspected Causes

1. **Confirmed: blocking post-push response path**. The emulator ran expensive
   post-push work before responding to `git-receive-pack`, so a large push could
   complete inside Git but still exceed proxy/client timeouts.
2. **Mitigated: large request buffering**. The request pack is now spooled to
   disk instead of being held in memory via `request.body()`.
3. **Not reproduced locally: many-ref `info/refs` authorization failure**. A
   regression test covers authenticated receive-pack discovery with 600 refs,
   and a live mirror-push smoke covered 700 refs.

## Workaround

Use the admin import API (`POST /api/v3/admin/repos/import`) instead of local clone + push. This performs the clone server-side and avoids the Smart HTTP transport entirely.

## Verification

- `uv run --with pytest --with pytest-asyncio pytest tests/test_git_http.py tests/test_git_integration.py -v`
  passed: 18 tests.
- `scripts/reproduce-large-git-push.sh` against
  `scripts/git-large-push-api.sh` successfully pushed a synthetic mirror repo
  with 700 refs and 200 files via Smart HTTP.
- The live server log showed the expected sequence:
  unauthenticated `GET /info/refs?service=git-receive-pack` returned 401,
  authenticated retry returned 200, `POST /git-receive-pack` returned 200, and
  `git ls-remote` confirmed `refs/heads/synthetic/700`.

## Environment

- GitHub emulator running in K8s (ai-pipeline namespace)
- Accessed via Caddy reverse proxy at `github.local`
- Git client: system git
