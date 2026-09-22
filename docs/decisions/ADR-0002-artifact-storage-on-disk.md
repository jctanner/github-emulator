# ADR-0002: Store Actions Artifacts on Disk, Indexed by the Database

## Status

Accepted

## Context

The Actions artifact API existed but was only half usable.

Uploads were accepted as `{"name": ..., "files": {path: content}}` and the
whole payload was stored in a JSON column on `workflow_artifacts`. The row's
`archive_download_url` advertised
`/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/{name}`, and no route
served that path. So an artifact could be stored and could not be retrieved.
Nothing outside this repository's own fidelity test used the API, which is
presumably why the gap went unnoticed.

Storing content in the database has three further problems here:

- Blobs bloat a SQLite file that is also the emulator's operational database.
  A large push has already caused an out-of-memory kill in a deployment of
  this emulator.
- Binary content cannot go in a JSON column without base64, which inflates it
  further and complicates the client.
- It invites a size cap, which is a policy decision the emulator should not
  need to make for a test fixture.

Meanwhile the emulator already solves exactly this problem for job logs: the
bytes live at `DATA_DIR/logs/jobs/{job_id}.log` and the database holds only
what is needed to find and describe them. `DATA_DIR` is a mounted volume in
the deployed configuration, so it survives a restart.

The consuming use case is a workflow whose evidence — an agent result file,
collected sandbox logs — is written into a job workspace that the runner
deletes when the job ends. Without durable storage, the evidence needed to
diagnose a failed run is gone before anyone reads the failure.

## Decision

Artifact bytes are stored on disk under `DATA_DIR/artifacts/{artifact_id}/`.
The `workflow_artifacts` row keeps `files` as an index of relative path to
size, plus name and total size. Deleting an artifact removes the directory.

Member paths are resolved through a helper that rejects any path escaping the
artifact directory. Paths come from whoever uploaded the artifact, so `../`
must be refused rather than normalised: a stored path resolving outside the
directory would let an upload overwrite or read back unrelated emulator data.
A failed write removes both the directory and the row rather than leaving an
index pointing at a partial upload.

Uploads accept two content types. `application/zip` takes the archive as the
request body with `?name=`, which carries binary content and directory
structure without encoding and is what a runner uploading a directory uses.
`application/json` keeps the original `{name, files}` shape for tests and
small text payloads. GitHub's own upload path is an internal Actions service
protocol rather than a documented REST endpoint, so there is no public shape
to be faithful to; both forms write to disk identically.

Downloads are served two ways, deliberately:

- `GET .../actions/artifacts/{artifact_id}/{archive_format}` matches GitHub's
  URL shape, accepts only `zip`, and streams the archive. GitHub answers with
  a 302 to a signed storage URL; serving bytes directly is equivalent from a
  client's point of view and avoids inventing a signing scheme.
  `archive_download_url` now points here, with `zip` in the format slot rather
  than the artifact name.
- `GET .../actions/artifacts/{artifact_id}/files/{path}` serves a single file.
  This is an emulator extension, not a GitHub endpoint. Reading one result
  file out of a failed run is the common case in this project, and requiring a
  zip round-trip for it is the kind of friction that stops people looking.

`GET .../actions/artifacts/{artifact_id}` continues to return `files`, now as
an index of path to size rather than content. This is also an extension; the
reason the emulator stores artifacts at all is to make a failed run
inspectable, and an index makes that possible without unpacking anything.

An expired artifact returns 410 from both download routes, matching GitHub.

## Consequences

The `files` column changes meaning from content to index. Any database
carrying artifacts written under the previous scheme holds content where sizes
are now expected. Nothing consumes that data, and artifacts are ephemeral test
fixtures, so no migration is provided.

The API gains a dependency on `DATA_DIR` being writable and durable. That was
already true for job logs.

Being faithful on the download URL means the previously advertised
`/{artifact_id}/{name}` shape is gone. It never worked, so nothing can break.
