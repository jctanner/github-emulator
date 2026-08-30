"""Shared GitHub-style verification payloads for emulator objects."""

from fastapi import Request


def verification(request: Request) -> dict:
    """Return the emulator's commit/tag verification result for this request.

    GitHub App installation-token writes are treated as verified by the
    emulator. Other writes remain explicitly unsigned.
    """
    verified = bool(getattr(request.state, "is_installation_token", False))
    return {
        "verified": verified,
        "reason": "valid" if verified else "unsigned",
        "signature": None,
        "payload": None,
    }
