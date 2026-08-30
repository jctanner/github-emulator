"""Signed browser-session and CSRF helpers for the same-origin frontend."""

import hashlib
import hmac

from jose import JWSError, jws

from app.config import settings


ALGORITHM = "HS256"
COOKIE_NAME = "ui_session"
CSRF_HEADER = "X-CSRF-Token"


def sign_browser_session(username: str) -> str:
    return jws.sign(
        username.encode("utf-8"),
        settings.SECRET_KEY,
        algorithm=ALGORITHM,
    )


def verify_browser_session(token: str) -> str | None:
    try:
        payload = jws.verify(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        return payload.decode("utf-8")
    except (JWSError, UnicodeDecodeError):
        return None


def csrf_token_for(session_token: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        f"csrf:{session_token}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def csrf_token_matches(session_token: str, supplied_token: str | None) -> bool:
    if not supplied_token:
        return False
    return hmac.compare_digest(csrf_token_for(session_token), supplied_token)
