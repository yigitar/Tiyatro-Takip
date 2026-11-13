from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.conftest import FakeStore
from tiyatro.heartbeat import github_run_status, pc_status, read_heartbeat, write_heartbeat

NOW = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)


def state(**statuses):
    return {"plays": {"p": {"sessions": {k: {"status": v} for k, v in statuses.items()}}}}


def hb(age_s, observed):
    return {"ts": (NOW - timedelta(seconds=age_s)).isoformat(), "last_scan_ok": None,
            "observed": observed}


def test_write_read_roundtrip():
    st = FakeStore()
    assert read_heartbeat(st) is None
    write_heartbeat(st, NOW, NOW - timedelta(seconds=30), {"s1": "on_sale"})
    h = read_heartbeat(st)
    assert h["observed"] == {"s1": "on_sale"}
    assert h["ts"] == NOW.isoformat()
    assert h["last_scan_ok"] == (NOW - timedelta(seconds=30)).isoformat()
    write_heartbeat(st, NOW, None, {})
    assert read_heartbeat(st)["last_scan_ok"] is None


def test_inactive_when_none_or_stale():
    assert pc_status(None, state(), NOW) == "inactive"
    assert pc_status(hb(11 * 60, {}), state(), NOW) == "inactive"


def test_active_when_fresh_and_matching():
    assert pc_status(hb(60, {"s1": "on_sale"}), state(s1="on_sale"), NOW) == "active"


def test_diverged_on_one_difference():
    h = hb(60, {"s1": "on_sale", "s2": "upcoming"})
    assert pc_status(h, state(s1="on_sale", s2="on_sale"), NOW) == "diverged"


def test_ignores_gone_and_unshared():
    h = hb(60, {"s1": "on_sale", "s2": "gone", "s3": "sold_out"})
    assert pc_status(h, state(s1="on_sale", s2="on_sale"), NOW) == "active"
    h = hb(60, {"s1": "on_sale"})
    assert pc_status(h, state(s1="gone"), NOW) == "active"


def run(age_min, conclusion="success"):
    t = (NOW - timedelta(minutes=age_min)).isoformat()
    return {"created_at": t, "run_started_at": t, "status": "completed",
            "conclusion": conclusion}


def test_run_status_empty():
    r = github_run_status([], NOW)
    assert r["healthy"] is False and r["last_run"] is None


def test_run_status_gap_unhealthy():
    r = github_run_status([run(20), run(25)], NOW)
    assert r["gap_s"] == 1200 and r["healthy"] is False


def test_run_status_lag_math_and_healthy():
    r = github_run_status([run(2), run(9)], NOW)  # interval 7 min, grid 5 -> lag 120
    assert r["lag_s"] == 120 and r["gap_s"] == 120 and r["healthy"] is True
    assert r["last_run"] == (NOW - timedelta(minutes=2)).isoformat()
    assert github_run_status([run(2), run(4)], NOW)["lag_s"] == 0
    assert github_run_status([run(2)], NOW)["lag_s"] == 0


def test_run_status_two_failures_unhealthy():
    assert github_run_status([run(2, "failure"), run(7, "failure")], NOW)["healthy"] is False
    assert github_run_status([run(2, "failure"), run(7)], NOW)["healthy"] is True
