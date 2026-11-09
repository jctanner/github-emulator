# Bug: steps.<id>.outcome is never set, and continue-on-error is ignored

Status: fixed 2026-11-08.

## Summary

The emulator runner exposed only `steps.<id>.outputs` to later steps.
`steps.<id>.outcome` and `steps.<id>.conclusion` read as missing, so any
condition comparing them with `'success'` was false.

Fullsend's reusable dispatch guards its whole CEL route on
`steps.cel-config.outcome == 'success'`. Here is what happened:

- the config checkout ran;
- all nine guarded steps were skipped;
- dispatch fell back to the bash route.

As a result, the agent triggers in `.fullsend` config had no effect. A
trigger meant to switch an agent off was ignored, and the agent ran anyway.
In Breadboard's behaviour-test T2 tier, this failed the on-demand triage,
review and prioritize trigger scenarios. Fullsend's install action has the
same dependency: its source-build fallback reads
`steps.download-release.outcome == 'failure'`.

`continue-on-error` was not passed to the runner either. A step that set it
still failed the job, and every later step without `if:` was skipped.

## Fix

- The runner records every step it runs or skips, in jobs and in composite
  actions, as a `_StepRecord`: the step's outputs, plus its `outcome` and its
  `conclusion`. A skipped step has outcome and conclusion `skipped`. The
  runner's `steps` context exposes all three, for conditions and for
  `${{ }}` in `env`, `with` and `run`.
- A failing step with `continue-on-error` has outcome `failure` and
  conclusion `success`. The job carries on as on GitHub, and the log says so.
- The server passes `continue-on-error` through to the runner.
  - It evaluates the setting to a value, because rendering it as text turned
    an input false into "False".
  - If the setting reads `steps.*`, the server binds the server-side
    contexts in it (`inputs.*`, `github.*`), as it does for a mixed step
    condition. It leaves the rest for the runner.
- The runner evaluates `continue-on-error` after the step has run, as
  GitHub does. A step can then read its own outputs:
  `continue-on-error: ${{ steps.probe.outputs.lenient == 'yes' }}`.
  Rendering the setting before the step ran froze that expression to false.

Tests: `tests/actions/test_step_outcome.py`.

Not covered:

- `continue-on-error` on a job;
- `steps.<id>.outcome` in a job's `outputs:`, which the server resolves.
