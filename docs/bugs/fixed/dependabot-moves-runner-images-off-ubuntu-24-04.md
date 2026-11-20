# Bug: Dependabot moves the runner images off Ubuntu 24.04

Status: fixed 2026-11-20 (issue #102).

## Summary

- `src/runners/emulator/Dockerfile` and `src/runners/upstream/Dockerfile`
  build `FROM ubuntu:24.04` on purpose: they stand in for GitHub's hosted
  `ubuntu-24.04` workers.
- The one docker entry in `.github/dependabot.yml` covered the root
  directory and both runner directories, so Dependabot proposed
  `ubuntu:25.10` for both runners (PRs #55 and #56, closed).
- 25.10 is an interim release, so the runners would no longer match the
  hosted workers. Dependabot also changes only the `FROM` line, and the
  upstream image installs `libicu74`, the 24.04 package name. That build
  fails on 25.10, and CI builds neither runner image.

## Fix

- The docker updates are split into two entries: `/` for the emulator
  images, and the two runner directories with their own `ignore` rule.
  The `ignore` rule is in a separate entry so it cannot affect the root
  images.
- The runner entry ignores `semver-major` and `semver-minor` updates of
  `ubuntu`. Dependabot reads `24.04` -> `24.10` as minor and `25.10` /
  `26.04` as major, so it proposes no new base release for either runner.
- This also blocks the next LTS. That is deliberate: moving the base also
  means moving the `libicu` pin and building and running both runners. A
  comment in the config says so.

## Evidence

- `.github/dependabot.yml` passes `check-yaml` and
  `check-jsonschema --builtin-schema vendor.dependabot` (the Dependabot v2
  schema): `ok -- validation done`.
- Whether Dependabot really stops proposing new base releases will show in
  its next weekly docker run.
