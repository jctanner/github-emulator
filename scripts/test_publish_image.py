"""Catch local-name RepoDigests and enforce publication failures and immutability."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("publish_image.sh")
DIGEST = "sha256:" + "a" * 64


class PublishImageTest(unittest.TestCase):
    def publish(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / "docker"
            docker.write_text(
                '#!/usr/bin/env bash\n'
                'case "$1" in\n'
                ' manifest)\n'
                '  if [[ "$MODE" == existing ]]; then exit 0; fi\n'
                '  if [[ "$MODE" == denied ]]; then echo denied >&2; exit 1; fi\n'
                '  echo "manifest unknown" >&2; exit 1;;\n'
                ' tag) exit 0;;\n'
                ' push)\n'
                '  if [[ "$MODE" == failed ]]; then exit 1; fi\n'
                '  if [[ "$MODE" == missing ]]; then echo "no digest"; exit 0; fi\n'
                f'  echo "$2: digest: {DIGEST} size: 1234";;\n'
                # Real Docker can list release-smoke@sha256 first. Never use it.
                ' *) echo "Unexpected docker operation: $1" >&2; exit 99;;\n'
                'esac\n'
            )
            docker.chmod(0o755)
            result = subprocess.run(
                ["bash", str(SCRIPT)], cwd=root, capture_output=True, text=True, check=False,
                env={**os.environ, "PATH": directory + os.pathsep + os.environ["PATH"],
                     "MODE": mode, "IMAGE_REPOSITORY": "ghcr.io/jctanner/example",
                     "GITHUB_REF": "refs/tags/2026.11.15", "GITHUB_REF_NAME": "2026.11.15",
                     "TAGS": "ghcr.io/jctanner/example:2026.11.15\nghcr.io/jctanner/example:sha-source"},
            )
            image = (root / "image.txt").read_text() if (root / "image.txt").exists() else None
            return result, image

    def test_registry_reference_uses_the_successful_push_digest(self):
        result, image = self.publish("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(image, f"ghcr.io/jctanner/example@{DIGEST}\n")

    def test_existing_release_registry_errors_and_push_failures_stop_publication(self):
        for mode in ["existing", "denied", "failed", "missing"]:
            with self.subTest(mode=mode):
                result, image = self.publish(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(image)
