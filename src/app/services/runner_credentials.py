"""The credential an upstream actions/runner proves itself with.

At registration the runner sends the public half of an RSA key it keeps
(``TaskAgent.authorization.publicKey``: base64 ``exponent`` and ``modulus``).
On the OAuth token exchange it sends a JWT client assertion signed with the
private half, whose ``iss`` and ``sub`` are its client id and whose ``aud``
is the authorization URL it was given. A runner token is issued only when
that assertion verifies against the key stored at registration.
"""

import base64
import binascii
import json
import math
import time
from collections.abc import Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

# Runner and emulator usually share a clock, but containers can drift.
CLOCK_SKEW_SECONDS = 60


class InvalidClientAssertion(Exception):
    """The assertion does not prove possession of the runner's registered key."""


def _decode_base64(value: str) -> bytes:
    """Decode standard or URL-safe base64, padded or not."""
    text = value.strip().replace("-", "+").replace("_", "/")
    text += "=" * (-len(text) % 4)
    return base64.b64decode(text, validate=True)


def public_key_pem_from_agent(body: dict) -> str | None:
    """PEM of the RSA key in a TaskAgent's authorization, or None if absent."""
    authorization = body.get("authorization") or body.get("Authorization")
    if not isinstance(authorization, dict):
        return None
    key = authorization.get("publicKey") or authorization.get("PublicKey")
    if not isinstance(key, dict):
        return None
    exponent = key.get("exponent") or key.get("Exponent")
    modulus = key.get("modulus") or key.get("Modulus")
    if not isinstance(exponent, str) or not isinstance(modulus, str):
        return None
    try:
        public_key = rsa.RSAPublicNumbers(
            int.from_bytes(_decode_base64(exponent), "big"),
            int.from_bytes(_decode_base64(modulus), "big"),
        ).public_key()
    except (binascii.Error, ValueError):
        return None
    return public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def _is_number(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        # Always finite. math.isfinite would convert to float and raise
        # OverflowError for a huge integer.
        return True
    return isinstance(value, float) and math.isfinite(value)


def verify_client_assertion(
    assertion: str,
    *,
    public_key_pem: str | None,
    client_id: str,
    audiences: Iterable[str],
    now: float | None = None,
) -> dict:
    """Return the assertion's claims, or raise InvalidClientAssertion.

    RS256 is what the runner signs with. It uses PSS padding instead of
    PKCS#1 v1.5 when told to require FIPS cryptography, so both paddings
    are accepted; either way the signature needs the registered key.
    """
    if not public_key_pem:
        raise InvalidClientAssertion("Runner has no registered public key")
    try:
        header_part, payload_part, signature_part = assertion.split(".")
        header = json.loads(_decode_base64(header_part))
        claims = json.loads(_decode_base64(payload_part))
        signature = _decode_base64(signature_part)
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
    except (ValueError, binascii.Error):
        raise InvalidClientAssertion("Malformed client assertion") from None
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise InvalidClientAssertion("Malformed client assertion")

    algorithm = header.get("alg")
    pss = padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.AUTO)
    if algorithm == "RS256":
        paddings = [padding.PKCS1v15(), pss]
    elif algorithm == "PS256":
        paddings = [pss]
    else:
        raise InvalidClientAssertion("Unsupported client assertion algorithm")

    public_key = serialization.load_pem_public_key(public_key_pem.encode("ascii"))
    for candidate in paddings:
        try:
            public_key.verify(signature, signing_input, candidate, hashes.SHA256())
            break
        except InvalidSignature:
            continue
    else:
        raise InvalidClientAssertion("Client assertion signature is invalid")

    if claims.get("iss") != client_id or claims.get("sub") != client_id:
        raise InvalidClientAssertion("Client assertion is not for this runner")

    audience = claims.get("aud")
    claimed = audience if isinstance(audience, list) else [audience]
    accepted = set(audiences)
    if not any(isinstance(value, str) and value in accepted for value in claimed):
        raise InvalidClientAssertion("Client assertion audience is invalid")

    now = time.time() if now is None else now
    expires = claims.get("exp")
    if not _is_number(expires) or expires + CLOCK_SKEW_SECONDS < now:
        raise InvalidClientAssertion("Client assertion has expired")
    not_before = claims.get("nbf")
    if not_before is not None and (
        not _is_number(not_before) or not_before - CLOCK_SKEW_SECONDS > now
    ):
        raise InvalidClientAssertion("Client assertion is not yet valid")
    return claims
