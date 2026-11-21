# Bug: Dependabot proposes interim Ubuntu releases for the runner images

Status: fixed 2026-11-21 (issue #102).

## Summary

- `src/runners/emulator/Dockerfile` and `src/runners/upstream/Dockerfile`
  stand in for GitHub's hosted Ubuntu workers. GitHub hosts LTS releases
  only (`ubuntu-24.04` and `ubuntu-26.04`).
- The one docker entry in `.github/dependabot.yml` covered the root
  directory and both runner directories, so Dependabot proposed
  `ubuntu:25.10` for both runners (PRs #55 and #56, closed).
- 25.10 is an interim release, so the runners would no longer match any
  hosted worker. Dependabot also changes only the `FROM` line, and the
  upstream image installs `libicu74`, the 24.04 package name. That build
  fails on any other release, and CI builds neither runner image.

## Fix

- The docker updates are split into two entries: `/` for the emulator
  images, and the two runner directories with their own `ignore` rule.
  The `ignore` rule is in a separate entry so it cannot affect the root
  images.
- The runner entry ignores `semver-major` and `semver-minor` updates of
  `ubuntu`. Dependabot reads `24.04` -> `24.10` as minor and `25.10` /
  `26.04` as major, so it proposes no new base release for either runner.
- Dependabot's `ignore` rules match update types and version ranges. They
  cannot tell an LTS release from an interim one, so the rule blocks the
  next LTS as well. LTS moves are manual: update the `libicu` package, then
  build and run both images on the new base. The config comment says this.
  Issue #105 tracks 26.04.

## Evidence

- `.github/dependabot.yml` passes `check-yaml` and
  `check-jsonschema --builtin-schema vendor.dependabot` (the Dependabot v2
  schema): `ok -- validation done`.
- Whether Dependabot really stops proposing new base releases will show in
  its next weekly docker run.
