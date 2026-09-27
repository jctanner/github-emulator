"""The memory watchdog: what it reports and when.

Three OOM kills at the cgroup limit, each with ~1.5 GiB in the Python
process and nothing naming a cause. The watchdog exists so the next one
leaves a report; these pin the two decisions that make that report useful
- *when* it fires and *what* it names - without a running server.
"""

import time

from app.services.memory_watch import InFlight, MemoryWatch

MIB = 1048576


def test_each_threshold_reports_once_and_in_order():
    w = MemoryWatch(thresholds=(500 * MIB, 800 * MIB))
    assert w.check(400 * MIB) is None
    assert w.check(520 * MIB) == "crossed 500MiB"
    assert w.check(530 * MIB) is None, "a crossed threshold does not fire again"
    assert w.check(900 * MIB) == "crossed 800MiB"
    assert w.check(950 * MIB) is None


def test_fast_growth_reports_relative_to_the_last_report():
    w = MemoryWatch(growth_step=200 * MIB)
    w._last_report_rss = 300 * MIB
    assert w.check(450 * MIB) is None
    assert w.check(510 * MIB) == "grew 210MiB since last report"


def test_peak_is_tracked():
    w = MemoryWatch()
    w.check(100 * MIB); w.check(300 * MIB); w.check(200 * MIB)
    assert w.peak == 300 * MIB


def test_report_names_in_flight_requests_oldest_first(tmp_path):
    w = MemoryWatch(report_path=str(tmp_path / "memory-watch.log"))
    w.in_flight[1] = InFlight("POST", "/api/v3/repos/o/r/issues", time.monotonic() - 17.0)
    w.in_flight[2] = InFlight("GET", "/api/v3/repos/o/r", time.monotonic() - 1.0)
    w.report(700 * MIB, "crossed 512MiB")
    text = (tmp_path / "memory-watch.log").read_text()
    assert "crossed 512MiB: rss=700MiB" in text
    assert text.index("POST /api/v3/repos/o/r/issues (17") < text.index("GET /api/v3/repos/o/r (1")
    assert w.reports == 1 and w._last_report_rss == 700 * MIB


def test_enter_and_leave_track_requests():
    w = MemoryWatch()
    k = w.enter("GET", "/x")
    assert len(w.in_flight) == 1
    w.leave(k)
    assert w.in_flight == {}
    w.leave(999)  # unknown keys are ignored
