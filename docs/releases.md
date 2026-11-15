# Releases and container images

Release versions use UTC CalVer: `YYYY.MM.DD`, followed by `.1`, `.2`, etc.
for further releases that day. Git tags, GitHub release names and image tags
are identical, without a `v` prefix. Existing release tags and images must
never be moved, deleted or overwritten. This is the deployment release version;
it does not change the Python distribution version or publish to PyPI.

After CI passes and the release PR is merged, fetch main, tag its exact commit
with an annotated CalVer tag, then push that tag. Check existing remote tags
before allocating the next version. The container workflow validates the date,
builds a Linux amd64 image once, smoke-tests it, then pushes that same local
image to GHCR. Only then does the reusable release workflow create a GitHub
release with generated notes, the source commit and the image digest.

Both `ghcr.io/jctanner/github-emulator` (standalone) and
`ghcr.io/jctanner/github-emulator-k3s` (Breadboard TLS/Caddy configuration) are
built and smoke-tested. The release waits for both variants and lists both
digests. Each repository's `latest` follows successful main builds.
`sha-<full commit SHA>` identifies the source revision. Releases also get their
exact CalVer tag; they do not update `latest`. Pull requests only build and
smoke-test. Manual runs on feature branches do not publish. Package visibility
is managed separately and requires human approval.

Pin deployments to the release tag and preferably its digest. A failed image
build creates no GitHub release. If publication partially succeeds, inspect
GHCR and Actions first; do not automatically rerun or overwrite a release
image. Fix the failure and allocate a new release version if necessary.

The container and release workflows are the reference pattern copied to the
other emulator and service repositories, with each repository's smoke check.
Repositories with an established release scheme retain that scheme.
