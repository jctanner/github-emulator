"""A runner that stops heartbeating reports offline.

The stored `status` is only written back to "offline" by `_requeue_stale_jobs`,
which reaches a runner solely when it is holding an in-progress job. A runner
that goes away while idle kept whatever it was last set to, so a stack that had
restarted its runners a few times served a list of runners all claiming to be
online — 87 of them in one case, the oldest heartbeat a month old — while three
processes were actually running. GitHub reports a runner that stops polling as
offline.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.api.actions_runners import _effective_status, _runner_payload
from app.config import settings
from app.models.actions import Runner


def _runner(**kw):
    r = Runner(name=kw.pop("name", "r1"), os="linux", labels=kw.pop("labels", ["self-hosted"]))
    r.id = kw.pop("id", 1)
    r.status = kw.pop("status", "online")
    r.busy = kw.pop("busy", False)
    r.last_heartbeat = kw.pop("last_heartbeat", datetime.now(timezone.utc))
    return r


def _ago(seconds):
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


def test_a_fresh_heartbeat_stays_online():
    assert _effective_status(_runner(last_heartbeat=_ago(1))) == "online"


def test_a_stale_heartbeat_reports_offline():
    stale = settings.RUNNER_STALE_THRESHOLD_SECONDS + 60
    assert _effective_status(_runner(last_heartbeat=_ago(stale))) == "offline"


def test_a_month_old_heartbeat_reports_offline():
    """The case actually observed: status online, heartbeat weeks old."""
    r = _runner(status="online", last_heartbeat=_ago(35 * 24 * 3600))
    assert _effective_status(r) == "offline"
    assert _runner_payload(r)["status"] == "offline"


def test_naive_timestamps_are_treated_as_utc():
    """SQLite returns naive datetimes; comparing them to an aware one raises."""
    naive_fresh = datetime.now(timezone.utc).replace(tzinfo=None)
    assert _effective_status(_runner(last_heartbeat=naive_fresh)) == "online"
    naive_stale = (_ago(settings.RUNNER_STALE_THRESHOLD_SECONDS + 60)).replace(tzinfo=None)
    assert _effective_status(_runner(last_heartbeat=naive_stale)) == "offline"


def test_no_heartbeat_at_all_is_offline():
    assert _effective_status(_runner(last_heartbeat=None)) == "offline"


def test_an_explicitly_offline_runner_stays_offline():
    """_requeue_stale_jobs writes "offline"; a fresh heartbeat must not undo it."""
    r = _runner(status="offline", last_heartbeat=_ago(1))
    assert _effective_status(r) == "offline"


def test_a_dead_runner_is_not_reported_busy():
    """Leaving busy=True on a dead runner is how a phantom looks occupied."""
    stale = settings.RUNNER_STALE_THRESHOLD_SECONDS + 60
    payload = _runner_payload(_runner(busy=True, last_heartbeat=_ago(stale)))
    assert payload["status"] == "offline"
    assert payload["busy"] is False


def test_a_live_busy_runner_is_still_busy():
    payload = _runner_payload(_runner(busy=True, last_heartbeat=_ago(1)))
    assert payload["status"] == "online"
    assert payload["busy"] is True


def test_labels_survive_the_payload():
    payload = _runner_payload(_runner(labels=["self-hosted", "linux", "fullsend"]))
    assert [l["name"] for l in payload["labels"]] == ["self-hosted", "linux", "fullsend"]
