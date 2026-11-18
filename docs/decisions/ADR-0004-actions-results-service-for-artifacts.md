# ADR-0004: Serve the Actions Results Service for upload/download-artifact v4+

## Status

Accepted

## Context

`actions/upload-artifact` and `actions/download-artifact` from v4 on (through
`@actions/artifact` v2+) do not use the REST artifact API from ADR-0002. They
use a Twirp service (a small RPC protocol over HTTP with JSON bodies) at
`ACTIONS_RESULTS_URL`:

1. `CreateArtifact` returns a signed upload URL.
2. The action uploads the zip to that URL as an Azure block blob.
3. `FinalizeArtifact` registers the upload as an artifact.
4. `ListArtifacts` and `GetSignedArtifactURL` find an artifact and sign its
   download URL.

The run and job they act for come from the `Actions.Results:<run>:<job>` scope
of the job's `ACTIONS_RUNTIME_TOKEN`.

Before this change, the upstream runner reached none of that. The job message
carried no results endpoint, and the job token had no such scope. Every
current workflow that uploads an artifact failed: the action stopped with
`GHESNotSupportedError` before any request.

The action refuses to run unless `GITHUB_SERVER_URL`'s host is `github.com`, a
`*.ghe.com` host or a `*.localhost` host. It treats every other host as GHES,
where it does not support v4+. The emulator cannot change that check from the
server side.

## Decision

- Implement the four Twirp methods in `src/app/api/actions_results.py`.
  - They authenticate with the job token, through `validate_job_token`.
  - They refuse backend ids that differ from the token's.
- Add the `Actions.Results:<run_id>:<job_id>` scope to the job token.
- Send `ResultsServiceUrl` in the job message's `SystemVssConnection` endpoint
  data. The runner exports it to node actions as `ACTIONS_RESULTS_URL`.
- Store uploads as follows:
  - A pending upload is a directory under `DATA_DIR/artifact-uploads/`.
  - Block uploads (`comp=block`, `comp=blocklist`) and a single Put Blob write
    into it.
  - `FinalizeArtifact` checks the reported sha256 and stores the content
    through `app.services.artifact_storage`, the same path as the REST upload.
    REST and v4 artifacts are therefore one kind of artifact.
  - An upload that isn't a zip (`archive: false`) is stored as one file under
    the artifact's name.
- Sign upload and download URLs with an HMAC of their purpose and id under
  `SECRET_KEY`. That is the only credential the Azure client sends.
- Read block lists with a pattern, not an XML parser. Only the ordered block
  ids matter, and a parser would process entities from the request body.

## Consequences

- To use v4+ artifact actions against the emulator, run it under a
  `*.localhost` host name, such as `ghemu.localhost`. On systemd-resolved hosts
  that name resolves to loopback with no configuration.
- `DeleteArtifact` and the migration-era methods are not served. The actions
  need neither to upload or download.
- Pending uploads that are never finalized stay on disk until the data
  directory is reset. Test stacks are short-lived, so nothing collects them.
