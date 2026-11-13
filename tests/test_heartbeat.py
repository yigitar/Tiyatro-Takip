from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

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


def test_pc_status_naive_timestamp_assumed_utc():
    naive = (NOW - timedelta(seconds=60)).replace(tzinfo=None).isoformat()
    assert pc_status({"ts": naive, "observed": {}}, state(), NOW) == "active"
    old = (NOW - timedelta(minutes=20)).replace(tzinfo=None).isoformat()
    assert pc_status({"ts": old, "observed": {}}, state(), NOW) == "inactive"


@pytest.mark.parametrize("ts", [None, "", "garbled", 12345, [1]])
def test_pc_status_bad_timestamp_inactive(ts):
    assert pc_status({"ts": ts, "observed": {}}, state(), NOW) == "inactive"
    assert pc_status({"observed": {}}, state(), NOW) == "inactive"


def test_pc_status_garbled_observed_does_not_raise():
    h = hb(60, None)
    assert pc_status(h, state(s1="on_sale"), NOW) == "active"
    assert pc_status({**hb(60, {}), "observed": "x"}, state(), NOW) == "active"


def test_run_status_naive_and_bad_timestamps():
    naive = (NOW - timedelta(minutes=2)).replace(tzinfo=None).isoformat()
    r = github_run_status([{"created_at": naive, "conclusion": "success"}], NOW)
    assert r["healthy"] is True and r["gap_s"] == 120
    for bad in (None, "garbled", 5):
        r = github_run_status([{"created_at": bad}], NOW)
        assert r["healthy"] is False
    r = github_run_status([{}], NOW)
    assert r["healthy"] is False
    # a garbled previous run must not break the lag computation
    r = github_run_status([run(2), {"created_at": "bad"}], NOW)
    assert r["healthy"] is True and r["lag_s"] == 0


# ---- fix round 2: lateness measured from the previous run's end -------------------

def done_run(start_min, end_min, conclusion="success"):
    return {"created_at": (NOW - timedelta(minutes=start_min)).isoformat(),
            "updated_at": (NOW - timedelta(minutes=end_min)).isoformat(),
            "status": "completed", "conclusion": conclusion}


def test_long_burst_run_that_ended_recently_is_healthy():
    # previous run started 16 min ago, ran a 13-min burst, ended 3 min ago
    r = github_run_status([done_run(16, 3), done_run(21, 20)], NOW)
    assert r["gap_s"] == 180 and r["healthy"] is True
    assert r["lag_s"] == 0  # started 4 min after the previous run ended (< 5 min grid)


def test_previous_run_ended_40_min_ago_is_unhealthy():
    r = github_run_status([done_run(41, 40), done_run(46, 45)], NOW)
    assert r["gap_s"] == 2400 and r["healthy"] is False


def test_gap_threshold_is_600_s():
    assert github_run_status([done_run(11, 10)], NOW)["healthy"] is True
    assert github_run_status([done_run(12, 11)], NOW)["healthy"] is False


def test_lag_is_start_vs_previous_end_plus_grid():
    # previous ended 20 min ago, latest started 12 min ago: 8 min later, 3 min late
    r = github_run_status([done_run(12, 2), done_run(30, 20)], NOW)
    assert r["lag_s"] == 180


def test_updated_at_missing_falls_back_to_created_at():
    r = github_run_status([{"created_at": (NOW - timedelta(minutes=8)).isoformat(),
                            "status": "completed", "conclusion": "success"}], NOW)
    assert r["gap_s"] == 480 and r["healthy"] is True
