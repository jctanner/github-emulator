"""The memory watchdog: what it reports and when.

Three OOM kills at the cgroup limit, each with ~1.5 GiB in the Python
process and nothing naming a cause. The watchdog exists so the next one
leaves a report; these pin the two decisions that make that report useful
- *when* it fires and *what* it names - without a running server.
"""

import time

import tracemalloc

from app.services.memory_watch import InFlight, MemoryWatch, _site, route_key

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


def test_a_site_is_named_by_our_innermost_frame_then_the_allocator():
    """A traceback iterates oldest frame first, so the allocation is last.

    The constructor takes frames the other way round, newest first, which is
    how the C side stores them; what matters here is the iteration order.
    """
    tb = tracemalloc.Traceback([
        ("/usr/local/lib/python3.12/json/decoder.py", 354),
        ("/usr/local/lib/python3.12/site-packages/sqlalchemy/sql/sqltypes.py", 2700),
        ("/app/src/app/services/workflow_service.py", 700),
        ("/app/src/app/api/actions.py", 212),
        ("/usr/local/lib/python3.12/asyncio/events.py", 80),
    ])
    assert [f.lineno for f in tb] == [80, 212, 700, 2700, 354]
    assert _site(tb) == "src/app/services/workflow_service.py:700  <- decoder.py:354"


def test_a_site_with_no_application_frame_falls_back_to_the_allocator():
    tb = tracemalloc.Traceback([("/usr/local/lib/python3.12/json/decoder.py", 354)])
    assert _site(tb) == "/usr/local/lib/python3.12/json/decoder.py:354"


def test_route_key_collapses_ids():
    assert route_key("POST", "/api/v3/repos/o/r/actions/runner/jobs/5356/logs") == \
        "POST /api/v3/repos/o/r/actions/runner/jobs/{id}/logs"
    assert route_key("GET", "/enterprises/b/_apis/distributedtask/pools/1/messages") == \
        "GET /enterprises/b/_apis/distributedtask/pools/{id}/messages"
    assert route_key("GET", "/api/v3/repos/o/r") == "GET /api/v3/repos/o/r"


def test_report_counts_completed_requests_by_endpoint(tmp_path):
    """The invisible shape: many quick requests, none in flight at report time."""
    w = MemoryWatch(report_path=str(tmp_path / "memory-watch.log"))
    for i in range(300):
        w.leave(w.enter("POST", f"/api/v3/repos/o/r/actions/runner/jobs/{i}/logs"))
    for _ in range(3):
        w.leave(w.enter("GET", "/api/v3/repos/o/r"))
    w.report(600 * MIB, "crossed 512MiB")
    text = (tmp_path / "memory-watch.log").read_text()
    assert "since last report: 303 requests completed" in text
    assert "by count:    300" in text and "POST /api/v3/repos/o/r/actions/runner/jobs/{id}/logs" in text
    # Counters reset, so the next report describes only its own interval.
    w.report(700 * MIB, "crossed 640MiB")
    assert "since last report" not in (tmp_path / "memory-watch.log").read_text().split("crossed 640MiB")[1]
