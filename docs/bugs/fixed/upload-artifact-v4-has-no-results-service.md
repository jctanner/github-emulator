# upload-artifact and download-artifact v4+ have no results service

## Symptom

On the upstream runner, every `actions/upload-artifact` and
`actions/download-artifact` step from v4 on failed. Only v3 and older, which
use the REST artifact API, worked.

## Cause

From v4 on, the actions (through `@actions/artifact` v2+) call a Twirp service
at `ACTIONS_RESULTS_URL` and move the bytes through signed Azure block-blob
URLs. The emulator served no such service. The job message gave the runner no
`ResultsServiceUrl`, so the actions had no `ACTIONS_RESULTS_URL`, and the job
token carried no `Actions.Results:<run>:<job>` scope for them to read the run
and job from.

## Fix

`src/app/api/actions_results.py` serves `CreateArtifact`, `FinalizeArtifact`,
`ListArtifacts`, `GetSignedArtifactURL` and `DeleteArtifact`, and the signed,
expiring block-blob upload and download URLs. The job token carries the
`Actions.Results` scope, and the job message's `SystemVssConnection` endpoint
carries `ResultsServiceUrl`. A download returns the uploaded blob byte for
byte. A finalized upload is also stored through `app.services.artifact_storage`,
the same path as the REST upload, so the REST routes see it. ADR-0004 records
the design, including the size limits.

The actions still refuse any server host but `github.com`, `*.ghe.com` and
`*.localhost`, so a stack that runs them needs a `*.localhost` host name.

## Regression coverage

`tests/actions/test_results_artifacts.py` covers:

- the job message's results URL;
- an upload, list and download round trip;
- byte-for-byte downloads of a zip, an unarchived file and an unarchived `.zip`;
- `overwrite: true` (delete, then create the same name);
- a retried `CreateArtifact` and a retried `FinalizeArtifact`;
- the uploaded size of a compressed artifact;
- a duplicate name, including two finalizes that race for one name;
- a hash mismatch, a missing or mismatched runtime token;
- a wrong or expired URL signature;
- the size, block-count and expanded-size limits.
