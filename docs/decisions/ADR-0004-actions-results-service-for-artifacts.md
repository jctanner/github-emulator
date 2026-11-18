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
5. With `overwrite: true`, upload-artifact first calls `DeleteArtifact` for an
   artifact of the same name.

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

- Implement the five Twirp methods in `src/app/api/actions_results.py`.
  - They authenticate with the job token, through `validate_job_token`.
  - They refuse backend ids that differ from the token's.
- Add the `Actions.Results:<run_id>:<job_id>` scope to the job token.
- Send `ResultsServiceUrl` in the job message's `SystemVssConnection` endpoint
  data. The runner exports it to node actions as `ACTIONS_RESULTS_URL`.
- Store uploads as follows:
  - A pending upload is a directory under `DATA_DIR/artifact-uploads/`.
  - Each run, job and name has one active pending upload. A new
    `CreateArtifact` replaces the earlier one, and `FinalizeArtifact` uses the
    active one.
  - Block uploads (`comp=block`, `comp=blocklist`) and a single Put Blob are
    streamed to disk.
  - `FinalizeArtifact` checks the reported sha256. It keeps the blob as
    uploaded under `DATA_DIR/artifact-blobs/`, and a download returns it byte
    for byte, with its content type and file name.
  - For the REST routes, the same call stores the files through
    `app.services.artifact_storage`, as the REST upload does. A zip is
    expanded into its members. An `archive: false` upload, which the client
    names after its one file, is stored as that file, even when it is a `.zip`.
  - A retried `FinalizeArtifact` with the committed sha256 returns the
    committed artifact.
- Sign upload and download URLs with an HMAC of their purpose, id and expiry
  under `SECRET_KEY`. That is the only credential the Azure client sends. They
  expire after 6 hours, the longest a job runs, and an expired URL is refused
  before its body is read.
- Bound an upload by `ARTIFACT_MAX_BYTES` (10 GiB by default): a blob, a
  committed block list, and what a zip expands to. Bound a block list by
  `ARTIFACT_MAX_BLOCKS` (50,000, Azure's limit). A refused commit leaves no
  blob behind.
- Read block lists with a pattern, not an XML parser. Only the ordered block
  ids matter, and a parser would process entities from the request body.

## Consequences

- To use v4+ artifact actions against the emulator, run it under a
  `*.localhost` host name, such as `ghemu.localhost`. On systemd-resolved hosts
  that name resolves to loopback with no configuration. The Compose stack's
  runner reaches the emulator as `ghemu.local`, so v4+ artifact actions don't
  run there.
- An archived artifact whose own name ends in `.zip` is indexed for REST as
  one file. Its download, which the actions use, is unaffected.
- The migration-era methods are not served. The actions need them neither to
  upload nor to download.
- A results-service artifact is on disk twice: its blob, and its files for
  REST.
- Pending uploads that are never finalized stay on disk until the data
  directory is reset. Test stacks are short-lived, so nothing collects them.
