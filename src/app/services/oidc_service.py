"""Ephemeral OIDC issuer used by the resettable Actions emulator."""

import base64
import hashlib
import time
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from jose import jwt

from app.config import settings

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_kid = hashlib.sha256(
    _key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
).hexdigest()[:16]


def issuer() -> str:
    return (getattr(settings, "OIDC_ISSUER", "") or settings.BASE_URL).rstrip("/")


def _b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def jwks() -> dict:
    numbers = _key.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": _kid, "n": _b64(numbers.n), "e": _b64(numbers.e)}]}


def issue(subject: str, audience: str) -> str:
    now = int(time.time())
    repository = subject.split(":", 2)[1] if subject.startswith("repo:") else subject
    repository_owner = repository.split("/", 1)[0] if "/" in repository else repository
    return jwt.encode(
        {"iss": issuer(), "sub": subject, "aud": audience, "iat": now, "exp": now + 300,
         "repository": repository, "repository_owner": repository_owner,
         "job_workflow_ref": "fullsend-dev/fullsend/.github/workflows/m8-oidc.yml@refs/heads/main"},
        _key,
        algorithm="RS256",
        headers={"kid": _kid},
    )
