"""ON_SALE de-duplication across the two runners when one sees the same open episode
more than 3 minutes after the other sent (final wave 2, Important 1)."""
from __future__ import annotations

from datetime import timedelta

from tests.test_pc_service import make, tick_at
from tests.test_runner import NOW, _status, _two_runners, mkplay, sess
from tiyatro.models import Status


def _set(e, status, opens_at=None):
    e.web.set(mkplay(sessions=[sess(status=status, opens_at=opens_at)]))


def _count(e):
    return sum("Bilet alınabilir" in m for m in e.gh_tg.sent + e.tg.sent)


def _long_sold_out(monkeypatch, tmp_path, first=Status.SOLD_OUT, ticks=40):
    e = make(monkeypatch, tmp_path, plays=[mkplay(sessions=[sess(status=first)])])
    tick_at(e, NOW)
    for k in range(1, ticks):
        tick_at(e, NOW + timedelta(seconds=300 * k))
    return e, NOW + timedelta(seconds=300 * ticks)


def test_iade_after_tail_same_episode_4min_lag(monkeypatch, tmp_path):
    e, base = _long_sold_out(monkeypatch, tmp_path)
    _set(e, Status.ON_SALE)
    e.gh_run(base - timedelta(seconds=280))  # GitHub sends
    for s in (0, 16, 40):
        tick_at(e, base + timedelta(seconds=s))  # the PC sees the same open episode
    assert _count(e) == 1


def test_unannounced_first_opening_4min_lag(monkeypatch, tmp_path):
    e, base = _long_sold_out(monkeypatch, tmp_path, first=Status.UNKNOWN, ticks=5)
    _set(e, Status.ON_SALE)
    e.gh_run(base - timedelta(seconds=250))
    for s in (0, 16):
        tick_at(e, base + timedelta(seconds=s))
    assert _count(e) == 1


def test_pc_first_github_four_minutes_later(monkeypatch, tmp_path):
    e, base = _long_sold_out(monkeypatch, tmp_path)
    _set(e, Status.ON_SALE)
    tick_at(e, base)
    tick_at(e, base + timedelta(seconds=16))
    assert len(e.tg.sent) == 1
    e.gh_run(base + timedelta(minutes=4))
    assert _count(e) == 1


def test_real_second_iade_seen_by_both_still_alerts_once_each(monkeypatch, tmp_path):
    """The runner that witnessed the sold-out in between may alert again (3-min rule)."""
    e, base = _long_sold_out(monkeypatch, tmp_path)
    _set(e, Status.ON_SALE)
    e.gh_run(base - timedelta(seconds=280))
    tick_at(e, base)
    tick_at(e, base + timedelta(seconds=16))
    assert _count(e) == 1
    _set(e, Status.SOLD_OUT)
    e.gh_run(base + timedelta(minutes=2))
    tick_at(e, base + timedelta(minutes=3))
    _set(e, Status.ON_SALE)
    t = base + timedelta(minutes=10)
    e.gh_run(t)
    tick_at(e, t + timedelta(minutes=4, seconds=40))
    tick_at(e, t + timedelta(minutes=5))
    assert _count(e) == 2  # one per real episode, not one per runner


# ---- two bare runners (no Service) -------------------------------------------------

def test_short_iade_only_github_then_later_iade_only_pc_still_alerts(monkeypatch):
    env, pc = _two_runners(monkeypatch)
    day = NOW.replace(hour=10)
    _status(env, Status.ON_SALE)
    env.run(day)  # GitHub: #1
    pc.run(day + timedelta(seconds=5))
    _status(env, Status.SOLD_OUT)
    env.run(day + timedelta(minutes=1))
    pc.run(day + timedelta(minutes=1, seconds=5))
    # a short iade that only GitHub catches (#2)
    t2 = day.replace(hour=11)
    _status(env, Status.ON_SALE)
    env.run(t2)
    _status(env, Status.SOLD_OUT)
    env.run(t2 + timedelta(seconds=30))
    pc.run(t2 + timedelta(minutes=5))
    assert len(env.tg.sent) == 2
    # a later iade that only the PC catches (#3): it saw the sold-out after #2
    t3 = day.replace(hour=13)
    _status(env, Status.ON_SALE)
    pc.run(t3)
    pc.run(t3 + timedelta(seconds=20))
    assert len(pc.tg.sent) == 1 and "Bilet alınabilir" in pc.tg.sent[0]


def test_both_runners_see_the_same_iade_four_minutes_apart(monkeypatch):
    env, pc = _two_runners(monkeypatch)
    day = NOW.replace(hour=10)
    _status(env, Status.ON_SALE)
    env.run(day)
    pc.run(day + timedelta(seconds=5))
    _status(env, Status.SOLD_OUT)
    env.run(day + timedelta(minutes=1))
    pc.run(day + timedelta(minutes=1, seconds=5))
    t = day.replace(hour=14)
    _status(env, Status.ON_SALE)
    env.run(t)
    for s in (240, 256, 300, 360):
        pc.run(t + timedelta(seconds=s))
    env.run(t + timedelta(minutes=10))
    assert len(env.tg.sent) + len(pc.tg.sent) == 2  # #1 and the iade, once each




# ---- state bookkeeping ----------------------------------------------------------------

def test_diff_records_closed_seen_at_and_puts_it_in_the_on_sale_event():
    from tests.test_state import T0, WATCH, play, sess as ssess
    from tiyatro.state import diff, empty_state
    st, _ = diff(empty_state(), [play(sessions=[ssess(status=Status.SOLD_OUT)])], WATCH, T0)
    t1 = T0 + timedelta(minutes=7)
    rec = st["plays"]["p"]["sessions"]["s1"]
    assert rec["closed_seen_at"] == T0.isoformat()
    st, _ = diff(st, [play(sessions=[ssess(status=Status.SOLD_OUT)])], WATCH, t1)
    assert st["plays"]["p"]["sessions"]["s1"]["closed_seen_at"] == t1.isoformat()
    t2 = t1 + timedelta(minutes=5)
    st, ev = diff(st, [play(sessions=[ssess(status=Status.ON_SALE)])], WATCH, t2)
    assert ev[0].payload["closed_seen_at"] == t1.isoformat()
    # while on sale it does not move; a vanished session counts as seen closed
    t3 = t2 + timedelta(minutes=5)
    st, _ = diff(st, [play(sessions=[ssess(status=Status.ON_SALE)])], WATCH, t3)
    assert st["plays"]["p"]["sessions"]["s1"]["closed_seen_at"] == t1.isoformat()
    t4 = t3 + timedelta(minutes=5)
    st, _ = diff(st, [play(sessions=[])], WATCH, t4)
    assert st["plays"]["p"]["sessions"]["s1"]["closed_seen_at"] == t4.isoformat()


def test_fingerprint_ignores_closed_seen_at():
    from tiyatro.runner import _fingerprint
    a = {"meta": {}, "plays": {"p": {"sessions": {"s1": {"status": "sold_out",
                                                         "closed_seen_at": "x"}}}}}
    b = {"meta": {}, "plays": {"p": {"sessions": {"s1": {"status": "sold_out",
                                                         "closed_seen_at": "y"}}}}}
    assert _fingerprint(a) == _fingerprint(b)


def test_reconcile_copies_a_newer_closed_seen_at():
    from pc.service import Service
    local = {"emitted": {}, "outbox": [], "plays": {"p": {"sessions": {
        "s1": {"closed_seen_at": "2026-11-21T10:00:00+03:00"}}}}}
    github = {"emitted": {}, "outbox": [], "plays": {"p": {"sessions": {
        "s1": {"closed_seen_at": "2026-11-21T11:00:00+03:00"}}}}}
    Service._reconcile(local, github, NOW)
    assert local["plays"]["p"]["sessions"]["s1"]["closed_seen_at"] \
        == "2026-11-21T11:00:00+03:00"
