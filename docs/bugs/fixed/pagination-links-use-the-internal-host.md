# Pagination links use the internal host instead of the public base URL

## Symptom

On breadboard, `Link` header URLs point at the emulator's in-cluster name. Clients outside the
cluster, and sandboxes that may only reach `github.local`, cannot follow them:

```
GET https://github.local/api/v3/repos/experiment/markov-test-repo/issues/1/comments?per_page=1&since=...
Link: <https://github-emulator.ai-pipeline.svc.cluster.local/api/v3/repos/experiment/markov-test-repo/issues/1/comments?since=...&page=2&per_page=1>; rel="next", ...
```

The same response's body uses the public base URL (`url`, `html_url` and `comments_url` are all
`https://github.local/...`), as GitHub does. Only the links differ. The query fix
(`docs/bugs/fixed/pagination-links-drop-query-parameters.md`, 7b889bc) works; this is a separate
problem that the old code had too.

## Cause

`src/app/api/pagination.py` `pagination_links` builds each link from `request.url`, so the host
is whatever the request arrived with. On breadboard the ingress
(`breadboard/deploy/k8s/05-ingress-routes.yaml`) and the in-pod Caddy deliver it as
`github-emulator.ai-pipeline.svc.cluster.local` (`GITHUB_EMULATOR_HOSTNAME`/`PUBLIC_HOSTNAME` in
`breadboard/deploy/k8s/10-github-emulator.yaml`), while `GITHUB_EMULATOR_BASE_URL` is
`https://github.local`.

## Fix

Build links from the configured base URL (`settings.BASE_URL`, the one response bodies use), plus
the request's path and query, instead of from the request's scheme and host. Keep the API prefix
(`/api/v3`) the request came in on. Check whether anything else that echoes `request.url` back
to clients (redirects, `Location` headers, other `request.url`/`request.base_url` uses) has the
same problem.

## Regression coverage

A test that sends a request with a `Host` different from `BASE_URL`, as an ingress does, and
checks that every `Link` URL starts with `BASE_URL` and still keeps the query parameters.

## Found by

Markov github-workflow-example's handoff check after 7b889bc: a `per_page=1` request with `since`
on `github.local` returned a `next` link on the cluster hostname.

## Status

Fixed (2026-10-24).

## Resolution

`src/app/api/pagination.py` now builds links on `settings.BASE_URL`: the scheme and host come from
it, and the path and query are the request's own. A path prefix in `BASE_URL` is added once, when
the request's path does not already start with it. The `/api/v3` prefix the request came in on is
kept, because it is part of the request path.

Other places that echo the request back to clients:

- Redirects (`src/app/api/root.py`) and the runner protocol's `Location` header
  (`src/app/api/actions_runner_protocol.py`) are relative, so they are not affected.
- `actions_pipelines.py` and `actions_distributed_task.py` already prefer `settings.BASE_URL` and
  use the request host only when it is unset.
- `POST /actions/runner-registration` (`src/app/api/actions_dispatch.py`) uses the request host
  only when the runner sends no `url`, so the runner gets back the address it reached the emulator
  on. That is intended for in-cluster runners and is left as it is.

## Verification

- `tests/test_pagination_links.py::test_links_use_public_base_url_not_request_host` sends a
  request with `Host: github-emulator.ai-pipeline.svc.cluster.local` while `BASE_URL` is
  `https://github.local`, and checks that every link starts with the public URL and keeps
  `state` and `per_page`.
- `test_links_keep_base_url_path_prefix` checks that a `BASE_URL` path prefix appears once.
- Both fail against the previous helper and pass with the fix. Full suite passing.
