#!/usr/bin/env python3
"""Seed the GitHub Apps a fullsend standalone mint needs on a github-emulator.

For each role it creates one App on the emulator (the emulator generates the
App's private key), installs it on the organisation for all repositories,
and writes the key to PEM_DIR/<role>.pem, the layout fullsend's
FilesystemPEMAccessor reads. It prints the mint's ROLE_APP_IDS JSON on stdout.

App slugs follow fullsend's convention, <app set>-<role> (appsetup.AppSlug),
so a bot's login is <app set>-<role>[bot] and the mint's bot-role lookup
(GET /apps/{slug}) resolves it to the role. The app set comes from APP_SET
(default "fullsend").

The e2e App is the identity a behaviour harness acts as. It is created and
installed like the others but left out of ROLE_APP_IDS, because no mint role
uses it: a bot-role lookup for it must be a no-match.

No secret leaves the VM: the keys are made by the emulator for this run and
die with it. Idempotent: an existing App or installation is reused.

Usage: ci-seed-mint-apps.py BASE_URL ADMIN_TOKEN ORG PEM_DIR [ROLE ...]
       ci-seed-mint-apps.py token BASE_URL ADMIN_TOKEN ORG ROLE [SECONDS]
The second form prints an installation token (default lifetime one day) for
a seeded App, e.g. the e2e App a behaviour harness acts as.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

# The fullsend mint's roles; fix shares coder's App and key (PemSecretRole).
MINT_ROLES = ["fullsend", "triage", "coder", "review", "retro", "prioritize", "scribe"]
# Apps that are seeded but are not mint roles.
NON_ROLE_APPS = ["e2e"]
# Installation ceiling. The mint narrows each token to its role's permission
# set, so the App itself is granted the union.
APP_PERMISSIONS = {
    "actions": "write", "actions_variables": "write", "checks": "read", "contents": "write",
    "issues": "write", "metadata": "read", "organization_projects": "write", "packages": "read",
    "pull_requests": "write", "workflows": "write", "administration": "write", "secrets": "write",
    "variables": "write", "members": "read",
}


def app_set() -> str:
    value = os.environ.get("APP_SET", "fullsend").strip()
    if not value or not all(ch.isalnum() or ch == "-" for ch in value):
        sys.exit(f"APP_SET {value!r} must be letters, digits and hyphens")
    return value


def call(base: str, token: str, method: str, path: str, body: dict | None = None, prefix: str = "/api/v3") -> tuple[int, Any]:
    if not base.startswith(("http://", "https://")):
        sys.exit(f"BASE_URL {base!r} must be an http or https URL")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310 - the scheme is checked above
        f"{base}{prefix}{path}", data=data, method=method,
        headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "seed-mint-apps"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - the scheme is checked above
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return exc.code, raw.decode(errors="replace")


def must(status: int, payload: Any, ok: tuple[int, ...], what: str) -> Any:
    if status not in ok:
        sys.exit(f"{what}: HTTP {status} {payload}")
    return payload


def install(base: str, token: str, app_id: str, org: str, what: str) -> Any:
    return must(*call(base, token, "POST", f"/admin/apps/{app_id}/installations", {
        "account_login": org, "account_type": "Organization", "repositories": [],
    }), (200, 201), what)


def create_app(base: str, token: str, prefix: str, role: str, slug: str) -> Any:
    """Create the App with an id the emulator picks, so app sets and later
    runs never ask for the same id. A picked id that is already taken (409
    "App id already exists") is picked again."""
    body = {"name": f"{prefix} {role}", "slug": slug, "permissions": APP_PERMISSIONS}
    for _ in range(5):
        status, payload = call(base, token, "POST", "/admin/apps", body)
        if status == 409 and "App id" in str(payload):
            continue
        return must(status, payload, (201,), f"create App {slug}")
    sys.exit(f"create App {slug}: no free App id after 5 tries")


def seed(base: str, token: str, org: str, pem_dir: str, roles: list[str]) -> None:
    prefix = app_set()
    os.makedirs(pem_dir, mode=0o700, exist_ok=True)
    status, payload = call(base, token, "GET", f"/orgs/{org}")
    if status == 404:
        must(*call(base, token, "POST", "/admin/organizations", {"login": org, "admin": "admin"}), (201,), f"create org {org}")
    else:
        must(status, payload, (200,), f"get org {org}")

    role_app_ids: dict[str, str] = {}
    for role in roles:
        slug = f"{prefix}-{role}"
        status, app = call(base, token, "GET", f"/apps/{slug}")
        if status == 404:
            app = create_app(base, token, prefix, role, slug)
            app_id = str(app["id"])
            key = app["private_key"]
        else:
            app_id = str(must(status, app, (200,), f"get App {slug}")["id"])
            key = must(*call(base, token, "GET", f"/admin/api/apps/{app_id}/private-key", prefix=""), (200,), f"key for {slug}")["private_key"]
        install(base, token, app_id, org, f"install {slug} on {org}")
        path = os.path.join(pem_dir, f"{role}.pem")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(key)
        if role not in NON_ROLE_APPS:
            role_app_ids[role] = app_id
    print(json.dumps(role_app_ids, separators=(",", ":")))


def print_token(base: str, token: str, org: str, role: str, seconds: int) -> None:
    """Print an installation token for one seeded App on the org.

    It uses the emulator's site-admin route, so it needs no App key and may
    live up to a day: long enough for a whole test suite to act as that App's
    bot (for example the e2e App, which a behaviour harness acts as).
    """
    slug = f"{app_set()}-{role}"
    app_id = str(must(*call(base, token, "GET", f"/apps/{slug}"), (200,), f"App {slug}")["id"])
    installation = install(base, token, app_id, org, f"installation of {slug} on {org}")
    minted = must(*call(base, token, "POST", f"/admin/apps/{app_id}/installations/{installation['id']}/access_tokens",
                        {"expires_in": seconds}), (201,), f"token for {slug}")
    print(minted["token"])


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "token":
        if len(sys.argv) not in (6, 7):
            sys.exit("usage: ci-seed-mint-apps.py token BASE_URL ADMIN_TOKEN ORG ROLE [SECONDS]")
        print_token(sys.argv[2].rstrip("/"), sys.argv[3], sys.argv[4], sys.argv[5],
                    int(sys.argv[6]) if len(sys.argv) == 7 else 86400)
        return
    if len(sys.argv) < 5:
        sys.exit(__doc__)
    seed(sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:] or MINT_ROLES + NON_ROLE_APPS)


if __name__ == "__main__":
    main()
