"""Ephemeral OIDC issuer used by the resettable Actions emulator."""

import base64
import hashlib
import time
import uuid
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


def subject_for(full_name: str, event: str, ref: str) -> str:
    """Derive the ``sub`` claim the way GitHub does.

    The subject is what a mint authorizes against, so it must describe the run
    rather than be chosen by the caller. Pull-request events get their own
    form because a token minted for a pull request should not satisfy a policy
    written for a branch.
    """
    if event in ("pull_request", "pull_request_target"):
        return f"repo:{full_name}:pull_request"
    return f"repo:{full_name}:ref:{ref}"


def issue_for_job(claims: dict, audience: str) -> str:
    """Issue an Actions OIDC token whose claims describe an actual run."""
    now = int(time.time())
    payload = {
        "iss": issuer(),
        "aud": audience,
        "iat": now,
        "nbf": now - 5,
        "exp": now + 300,
        "jti": uuid.uuid4().hex,
        **claims,
    }
    return jwt.encode(payload, _key, algorithm="RS256", headers={"kid": _kid})
