#!/usr/bin/env bash
# Publish only the locally smoke-tested image; Docker push reports the registry digest.
set -euo pipefail
image="${IMAGE_REPOSITORY,,}"
if [[ "$GITHUB_REF" == refs/tags/* ]]; then
  if docker manifest inspect "$image:$GITHUB_REF_NAME" > /dev/null 2>manifest-error; then
    echo "Release image already exists; immutable tags cannot be republished."
    exit 1
  fi
  if ! grep -Eqi 'manifest unknown|no such manifest|not found' manifest-error; then
    cat manifest-error
    exit 1
  fi
fi
pushed_digest=""
while IFS= read -r tag; do
  docker tag release-smoke:local "$tag"
  docker push "$tag" | tee push-output
  current_digest=$(sed -nE 's/.*digest: (sha256:[a-f0-9]{64}) size:.*/\1/p' push-output)
  if [[ ! "$current_digest" =~ ^sha256:[a-f0-9]{64}$ ]]; then
    echo "Docker push did not report exactly one registry digest."
    exit 1
  fi
  if [[ -n "$pushed_digest" && "$pushed_digest" != "$current_digest" ]]; then
    echo "Published tags resolved to different image digests."
    exit 1
  fi
  pushed_digest="$current_digest"
done <<< "$TAGS"
[[ -n "$pushed_digest" ]]
printf '%s@%s\n' "$image" "$pushed_digest" > image.txt
