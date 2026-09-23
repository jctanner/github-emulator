"""Repository public keys for Actions secrets.

GitHub does not accept a secret in plaintext. A client fetches the
repository's public key, seals the value into a libsodium sealed box, and
uploads the base64 ciphertext with the key id it sealed against. A client that
does this correctly — the `gh` CLI, the Fullsend CLI, actions/github-script —
gets a 404 from an emulator that never serves a public key, and the error it
reports names the missing endpoint rather than the secret it failed to set.

The keypair is derived, not stored. A repository's private key is
``sha256(salt || repo_id)``, which is exactly the 32 bytes Curve25519 wants.
That keeps the endpoint stable across restarts and across a database wipe with
no new table and no migration, which matters for an emulator whose reset path
clears PVC contents. The salt comes from ``GITHUB_EMULATOR_SECRET_SALT`` when
set, so two deployments can be made to differ.

This is a development emulator: the opened plaintext is stored so that a run
can actually use the secret, in the `value` column that already exists for the
local seed path. Nothing returns it through the API.
"""

import hashlib
import os

from nacl.public import PrivateKey, PublicKey, SealedBox
from nacl.encoding import Base64Encoder

# Not a secret in any meaningful sense — it exists so that two emulators can
# be given different keys, not to protect anything.
_DEFAULT_SALT = "github-emulator-actions-secrets"


def _salt() -> bytes:
    return os.environ.get("GITHUB_EMULATOR_SECRET_SALT", _DEFAULT_SALT).encode()


def repo_private_key(repo_id: int) -> PrivateKey:
    """Return the repository's Curve25519 private key.

    Derived rather than generated so it survives a restart and a data wipe
    without being persisted anywhere.
    """
    seed = hashlib.sha256(_salt() + str(repo_id).encode()).digest()
    return PrivateKey(seed)


def repo_public_key(repo_id: int) -> tuple[str, str]:
    """Return ``(key_id, base64_public_key)`` for a repository.

    The key id is a digest of the public key itself, so a client that cached
    one can tell whether it is still current.
    """
    private = repo_private_key(repo_id)
    public: PublicKey = private.public_key
    encoded = public.encode(Base64Encoder).decode()
    key_id = hashlib.sha256(public.encode()).hexdigest()[:16]
    return key_id, encoded


class SecretDecryptError(ValueError):
    """Raised when an encrypted_value cannot be opened for this repository."""


def open_sealed_secret(repo_id: int, encrypted_value: str) -> str:
    """Open a base64 sealed box that was sealed to this repository's key.

    Raised errors are deliberately specific. "Could not decrypt" on its own
    sends people looking at the secret's contents; the two real causes are
    sealing against a different repository's key and sending something that is
    not a sealed box at all.
    """
    try:
        ciphertext = Base64Encoder.decode(encrypted_value.encode())
    except Exception as exc:  # noqa: BLE001 - base64 raises several types
        raise SecretDecryptError(
            "encrypted_value is not valid base64; it must be a libsodium "
            "sealed box encoded with base64"
        ) from exc

    box = SealedBox(repo_private_key(repo_id))
    try:
        return box.decrypt(ciphertext).decode()
    except Exception as exc:  # noqa: BLE001 - nacl raises CryptoError subclasses
        raise SecretDecryptError(
            "encrypted_value could not be opened with this repository's key. "
            "Fetch the current key from "
            "GET /repos/{owner}/{repo}/actions/secrets/public-key and seal "
            "against that key_id."
        ) from exc
