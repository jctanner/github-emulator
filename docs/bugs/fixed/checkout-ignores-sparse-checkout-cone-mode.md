# Emulated checkout ignores `sparse-checkout-cone-mode: false`

## Symptom

A workflow step that checks out with gitignore-style sparse patterns fails before any of its
files arrive:

```yaml
- uses: actions/checkout@v4
  with:
    sparse-checkout: |
      .fullsend/config.yaml
      /OWNERS
      /OWNERS_ALIASES
    sparse-checkout-cone-mode: false
```

```
Checkout failed: fatal: specify directories rather than patterns (no leading slash)
```

Fullsend v0.52.0's `reusable-dispatch.yml` checks out its caller this way, so its Route and
Harness dispatch jobs failed and every later job was skipped.

Real `actions/checkout` defaults to cone mode and switches to non-cone mode when
`sparse-checkout-cone-mode` is `false`.

## Cause

`_checkout_step` in `src/runners/emulator/runner.py` always ran
`git sparse-checkout init --cone` and passed the patterns to `git sparse-checkout set` in cone
mode, whatever the step asked for. Cone mode accepts only directories.

## Fix

Read `sparse-checkout-cone-mode` (default `true`) and pass `--cone` or `--no-cone` to both
`git sparse-checkout init` and `git sparse-checkout set`.

## Regression coverage

The checkout step has no unit tests. The same git sequence was replayed against a scratch
repository holding `OWNERS`, `OWNERS_ALIASES`, `.fullsend/config.yaml`, `sub/OWNERS` and
`other`: cone mode fails with the error above, and `--no-cone` checks out exactly
`.fullsend/config.yaml`, `OWNERS` and `OWNERS_ALIASES`. Breadboard's Fullsend conformance run
then passed through the patched runner (run 1816).
