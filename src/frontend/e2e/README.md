# Frontend parity harness

`routes.ts` is the authoritative migration manifest. Routes progress from
`fallback` to `candidate` and then `parity`. Only `parity` routes are compared
structurally and pixel-for-pixel.

Run against a deterministically seeded emulator:

```bash
GITHUB_EMULATOR_URL=https://github.local npm run parity
```

When a legacy route is ready to become the accepted visual baseline:

```bash
UPDATE_PARITY_BASELINES=1 npm run parity
```

The seed must satisfy the fixture requirements recorded in
`docs/notes/frontend-route-api-matrix.md`. Owner, repository, issue, pull, and
run identifiers can be overridden with the `PARITY_*` environment variables.
Generated reports, traces, screenshots, and test results are ignored.
