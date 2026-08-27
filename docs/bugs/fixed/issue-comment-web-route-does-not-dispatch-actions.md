# Bug: Web issue comments did not dispatch Actions events

## Summary

Posting an issue comment through the emulator's browser UI created the comment
but did not emit the corresponding `issue_comment:created` Actions event.

## Reproduction

1. Open an issue in the emulator web UI.
2. Post `/fs-code` through the issue comment form.
3. Observe that the comment is present, but no target `issue_comment` workflow
   run is created.

The REST comment endpoint already dispatched the event, so this behavior differed
between the browser UI and GitHub's shared event semantics.

## Resolution

The web comment route now dispatches `issue_comment:created` after creating the
comment, using the same activity payload as the REST endpoint. Regression
coverage verifies the event, action, and comment body.

## Verification

- Focused web and Actions tests: `23 passed`.
- Rebuilt and redeployed with `make host-rebuild-github`.
- Browser-form comment created target run `875` with event `issue_comment`.
- Target routing job `1523` completed successfully.
- Central Code run `876` was created; its Code agent job was queued waiting for
  an available `fullsend` runner.
