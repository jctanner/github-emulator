"""One API write to a repository's refs at a time, held to the end of the request.

An API write moves a git ref and then records it, the pull request heads it
moves and the push it raises in the database. Two such writes to one
repository that interleave can leave the database pointing at the older
commit: a later writer's ref move lands between an earlier writer's move and
its record, or an earlier writer puts the ref back after the later one
skipped recording it. So a route takes its repository's lock before it reads
the ref it will move, and holds it until the response is sent:
``RequestContextMiddleware`` releases it. The emulator serves from one
process, so an in-process lock covers every API writer. Git pushes take
their own path and are not serialized by it.
"""

import asyncio
import contextvars

_locks: dict[tuple[int, int], asyncio.Lock] = {}
# The locks this request holds, by repository id; None outside a request
# that RequestContextMiddleware wraps (a direct service call holds none).
held: contextvars.ContextVar[dict | None] = contextvars.ContextVar("held_repository_locks", default=None)


async def hold_repository_lock(repository_id: int) -> None:
    """Take ``repository_id``'s write lock for the rest of this request."""
    locks = held.get()
    if locks is None or repository_id in locks:
        return
    # Keyed by event loop too: a lock waits on the loop it was first used in.
    lock = _locks.setdefault((id(asyncio.get_running_loop()), repository_id), asyncio.Lock())
    await lock.acquire()
    locks[repository_id] = lock


def release_held(locks: dict) -> None:
    """Release every lock a request took."""
    for lock in locks.values():
        lock.release()
    locks.clear()
