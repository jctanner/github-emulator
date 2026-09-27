"""Explain the next out-of-memory kill before it happens.

The emulator's uvicorn process has been OOM-killed at its cgroup limit three
times (2026-09-23 08:45 and 13:06, 2026-09-27 15:14), each time with about
1.5 GiB of anonymous RSS in the Python process itself - not in git, whose
children share the cgroup - and each time with nothing in any log naming a
request or an allocation. Replaying every operation from the last window
under a half-second sampler stayed under 350 MiB, so the cause does not
reproduce on demand and has to be caught in the act.

This module does three things, all cheap enough to leave on:

- samples the process RSS every few seconds from ``/proc/self/statm``;
- keeps the set of requests currently in flight, via a middleware;
- when RSS crosses a threshold or grows fast, writes a report - RSS, the
  in-flight requests with their age, and tracemalloc's largest allocation
  sites with their change since the previous report - to the log and to
  ``DATA_DIR/memory-watch.log``, which survives the container being killed
  in a way that stdout may not.

tracemalloc costs memory (a record per live allocation) and some CPU; with
``MEMORY_WATCH_FRAMES`` kept small it is a development-stack price, and it
is what turns "1.5 GiB in uvicorn" into a file and line.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import tracemalloc
from dataclasses import dataclass, field

from app.config import settings

logger = logging.getLogger("github_emulator.memory")

_PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def rss_bytes() -> int:
    """Resident set size of this process, from procfs; 0 where unavailable."""
    try:
        with open("/proc/self/statm", "rb") as fh:
            return int(fh.read().split()[1]) * _PAGE
    except (OSError, ValueError, IndexError):
        return 0


def _site(traceback: tracemalloc.Traceback) -> str:
    """Name an allocation by our innermost frame, then by the allocating one.

    tracemalloc orders a traceback oldest frame first, so the allocation
    itself is the last frame and the application frame nearest to it is the
    first match walking backwards.
    """
    frames = list(traceback)
    allocating = frames[-1]
    for frame in reversed(frames):
        if "/app/" in frame.filename and "memory_watch" not in frame.filename:
            return (
                f"{frame.filename.split('/app/', 1)[1]}:{frame.lineno}"
                f"  <- {allocating.filename.rsplit('/', 1)[-1]}:{allocating.lineno}"
            )
    return f"{allocating.filename}:{allocating.lineno}"


@dataclass
class InFlight:
    method: str
    path: str
    started: float

    def describe(self, now: float) -> str:
        return f"{self.method} {self.path} ({now - self.started:.1f}s)"


@dataclass
class MemoryWatch:
    """Sampler state; one per process."""

    interval: float = 2.0
    thresholds: tuple[int, ...] = ()  # bytes, ascending
    growth_step: int = 0  # bytes; report when RSS grew this much since the last report
    frames: int = 3
    top: int = 15
    report_path: str | None = None
    in_flight: dict[int, InFlight] = field(default_factory=dict)
    _next_key: int = 0
    _last_report_rss: int = 0
    _crossed: set[int] = field(default_factory=set)
    _previous: tracemalloc.Snapshot | None = None
    _task: asyncio.Task | None = None
    peak: int = 0
    reports: int = 0

    # -- request tracking ---------------------------------------------------

    def enter(self, method: str, path: str) -> int:
        self._next_key += 1
        self.in_flight[self._next_key] = InFlight(method, path, time.monotonic())
        return self._next_key

    def leave(self, key: int) -> None:
        self.in_flight.pop(key, None)

    # -- reporting ------------------------------------------------------------

    def snapshot_report(self, rss: int, reason: str) -> str:
        now = time.monotonic()
        lines = [
            f"memory-watch: {reason}: rss={rss / 1048576:.0f}MiB "
            f"peak={self.peak / 1048576:.0f}MiB in_flight={len(self.in_flight)}",
        ]
        for item in sorted(self.in_flight.values(), key=lambda i: i.started)[:25]:
            lines.append(f"  in flight: {item.describe(now)}")
        if tracemalloc.is_tracing():
            current = tracemalloc.take_snapshot()
            traced, traced_peak = tracemalloc.get_traced_memory()
            lines.append(
                f"  tracemalloc: traced={traced / 1048576:.0f}MiB "
                f"traced_peak={traced_peak / 1048576:.0f}MiB"
            )
            # Grouped by the full traceback, so two callers of json.loads
            # stay two rows, and named by the innermost frame in our own
            # code: "json/decoder.py:354" says what allocated, "_job_json
            # in actions.py:212" says who asked for it.
            for stat in current.statistics("traceback")[: self.top]:
                lines.append(
                    f"  top: {stat.size / 1048576:7.1f}MiB {stat.count:>7} blocks  "
                    f"{_site(stat.traceback)}"
                )
            if self._previous is not None:
                for stat in current.compare_to(self._previous, "traceback")[: self.top]:
                    if stat.size_diff <= 0:
                        continue
                    lines.append(
                        f"  grew: {stat.size_diff / 1048576:+7.1f}MiB "
                        f"{stat.count_diff:+8} blocks  {_site(stat.traceback)}"
                    )
            self._previous = current
        else:
            lines.append("  tracemalloc: off (MEMORY_WATCH_TRACE=0)")
        return "\n".join(lines)

    def report(self, rss: int, reason: str) -> None:
        text = self.snapshot_report(rss, reason)
        self.reports += 1
        self._last_report_rss = rss
        logger.warning("%s", text)
        if self.report_path:
            try:
                os.makedirs(os.path.dirname(self.report_path), exist_ok=True)
                with open(self.report_path, "a", encoding="utf-8") as fh:
                    fh.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + text + "\n\n")
            except OSError:
                logger.exception("memory-watch: could not write %s", self.report_path)

    def check(self, rss: int) -> str | None:
        """The reason to report now, or None."""
        if rss > self.peak:
            self.peak = rss
        for threshold in self.thresholds:
            if rss >= threshold and threshold not in self._crossed:
                self._crossed.add(threshold)
                return f"crossed {threshold / 1048576:.0f}MiB"
        if self.growth_step and rss - self._last_report_rss >= self.growth_step:
            return f"grew {(rss - self._last_report_rss) / 1048576:.0f}MiB since last report"
        return None

    # -- lifecycle ------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            try:
                rss = rss_bytes()
                reason = self.check(rss)
                if reason:
                    # A snapshot over a few hundred thousand live blocks
                    # takes seconds; off the loop, so a report never stalls
                    # the requests it is trying to describe.
                    await asyncio.to_thread(self.report, rss, reason)
            except Exception:  # never let the watchdog take the app down
                logger.exception("memory-watch: sampler error")
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        if settings.MEMORY_WATCH_TRACE and not tracemalloc.is_tracing():
            tracemalloc.start(self.frames)
        self._last_report_rss = rss_bytes()
        self.peak = self._last_report_rss
        self._task = asyncio.get_running_loop().create_task(self._run(), name="memory-watch")
        logger.info(
            "memory-watch: on, rss=%dMiB, thresholds=%s, growth_step=%dMiB, trace=%s",
            self._last_report_rss // 1048576,
            [t // 1048576 for t in self.thresholds],
            self.growth_step // 1048576,
            tracemalloc.is_tracing(),
        )

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    def status(self) -> dict:
        now = time.monotonic()
        return {
            "rss_bytes": rss_bytes(),
            "peak_bytes": self.peak,
            "reports": self.reports,
            "tracing": tracemalloc.is_tracing(),
            "in_flight": [i.describe(now) for i in sorted(self.in_flight.values(), key=lambda i: i.started)],
            "report_path": self.report_path,
        }


watch = MemoryWatch()


def configure_from_settings() -> MemoryWatch:
    mib = 1048576
    watch.interval = float(settings.MEMORY_WATCH_INTERVAL_SECONDS)
    watch.thresholds = tuple(
        sorted(int(x) * mib for x in str(settings.MEMORY_WATCH_THRESHOLDS_MIB).split(",") if x.strip())
    )
    watch.growth_step = int(settings.MEMORY_WATCH_GROWTH_MIB) * mib
    watch.frames = int(settings.MEMORY_WATCH_FRAMES)
    watch.report_path = os.path.join(settings.DATA_DIR, "memory-watch.log")
    return watch
