"""Atomic compare-and-swap updates of several refs (GraphQL ``updateRefs``).

GitHub's ``updateRefs`` creates, moves, or deletes refs in one repository,
all or none. Each update names the ref, the value it must have now
(``beforeOid``, all zeros for "must not exist"; omitted for no check), and
its new value (``afterOid``, all zeros to delete). Without ``force`` a move
must be a fast-forward. This module checks every update against the
repository on disk, then applies them in one ``git update-ref --stdin``
transaction whose old values are the ones checked, so a ref that moves in
between fails the whole batch instead of being overwritten.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass

ZERO_OID = "0" * 40
_OID = re.compile(r"^[0-9a-f]{40}$")


class RefUpdateError(Exception):
    """An update that cannot be applied; ``kind`` is GitHub's error type."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class PlannedUpdate:
    name: str
    before: str | None  # current value on disk; None when the ref is absent
    after: str | None  # None deletes
    forced: bool  # a move that is not a fast-forward

    @property
    def branch(self) -> str | None:
        return self.name.removeprefix("refs/heads/") if self.name.startswith("refs/heads/") else None


async def _git(repo_path: str, *args: str, stdin: bytes | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GIT_DIR": repo_path},
    )
    out, err = await proc.communicate(stdin)
    return proc.returncode, out.decode().strip(), err.decode().strip()


def _oid(value: str | None, field: str, name: str) -> str | None:
    if value is None:
        return None
    oid = str(value).strip().lower()
    if not _OID.match(oid):
        raise RefUpdateError("UNPROCESSABLE", f"{field} for {name} is not a valid object id: {value!r}")
    return oid


async def plan(repo_path: str, updates) -> list[PlannedUpdate]:
    """Check ``updates`` (objects with ``name``, ``before_oid``, ``after_oid``,
    ``force``) against the repository; raise ``RefUpdateError`` for the first
    one that cannot be applied."""
    if not updates:
        raise RefUpdateError("UNPROCESSABLE", "refUpdates must name at least one ref")
    seen: set[str] = set()
    planned: list[PlannedUpdate] = []
    for update in updates:
        name = str(update.name)
        if not name.startswith("refs/"):
            raise RefUpdateError("UNPROCESSABLE", f"{name} is not a fully qualified ref name (refs/...)")
        code, _, _ = await _git(repo_path, "check-ref-format", name)
        if code != 0:
            raise RefUpdateError("UNPROCESSABLE", f"{name} is not a valid ref name")
        if name in seen:
            raise RefUpdateError("UNPROCESSABLE", f"{name} is updated more than once")
        seen.add(name)

        expected = _oid(update.before_oid, "beforeOid", name)
        after = _oid(update.after_oid, "afterOid", name)
        if after == ZERO_OID:
            after = None
        code, current, _ = await _git(repo_path, "rev-parse", "-q", "--verify", f"{name}^{{object}}")
        current = current if code == 0 and current else None

        if expected is not None and (expected == ZERO_OID) != (current is None):
            raise RefUpdateError(
                "STALE_DATA",
                f"Expected {name} not to exist, but it points to {current}"
                if current else f"Expected {name} to point to {expected}, but it does not exist",
            )
        if expected not in (None, ZERO_OID) and expected != current:
            raise RefUpdateError("STALE_DATA", f"Expected {name} to point to {expected}, but it points to {current}")
        if after is None and current is None:
            raise RefUpdateError("UNPROCESSABLE", f"Cannot delete {name}: it does not exist")

        forced = False
        if after is not None:
            code, kind, _ = await _git(repo_path, "cat-file", "-t", after)
            if code != 0:
                raise RefUpdateError("UNPROCESSABLE", f"Object {after} for {name} does not exist")
            if name.startswith("refs/heads/") and kind != "commit":
                raise RefUpdateError("UNPROCESSABLE", f"{name} must point to a commit, not a {kind}")
            if current is not None and current != after:
                code, _, _ = await _git(repo_path, "merge-base", "--is-ancestor", current, after)
                forced = code != 0
                if forced and not update.force:
                    raise RefUpdateError("UNPROCESSABLE", f"Update to {name} is not a fast forward")
        planned.append(PlannedUpdate(name=name, before=current, after=after, forced=forced))
    return planned


async def apply(repo_path: str, planned: list[PlannedUpdate]) -> None:
    """Apply ``planned`` in one transaction, each against the value it was
    checked at; nothing changes if any ref moved since."""
    lines = []
    for update in planned:
        if update.before == update.after:
            # No change, but its beforeOid still has to hold when the
            # others apply.
            lines.append(f"verify {update.name} {update.before}")
        elif update.after is None:
            lines.append(f"delete {update.name} {update.before}")
        elif update.before is None:
            lines.append(f"create {update.name} {update.after}")
        else:
            lines.append(f"update {update.name} {update.after} {update.before}")
    if all(line.startswith("verify ") for line in lines):
        return
    code, _, err = await _git(repo_path, "update-ref", "--stdin", stdin=("\n".join(lines) + "\n").encode())
    if code != 0:
        raise RefUpdateError("STALE_DATA", f"A ref changed during the update; nothing was changed ({err})")
