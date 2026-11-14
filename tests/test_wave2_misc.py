"""Final wave 2: burst fetch-failure alert key stability (Minor 3)."""
from __future__ import annotations

from datetime import timedelta

from tests.test_runner import NOW, WATCH_CFG, Env, mkplay, sess
from tiyatro.fetcher import FetchError
from tiyatro.models import Status
from tiyatro.runner import burst_loop


def _burst_run(env, start, seconds):
    clock = [start]

    def sleep(s):
        clock[0] += timedelta(seconds=s)
        env.store.now = clock[0]

    env.store.now = start
    return burst_loop(env.store, env.tg, env.claims, lambda: clock[0], sleep,
                      start + timedelta(hours=1), fetch_fn=env.web.fetch,
                      rand=lambda a, b: 0.0, max_seconds=seconds)


def test_two_consecutive_burst_outage_runs_send_one_alert_without_opens_at(monkeypatch):
    env = Env(monkeypatch, [mkplay()], WATCH_CFG)
    env.run(NOW)
    t0 = NOW + timedelta(minutes=5)
    env.web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))  # no opens_at known
    env.run(t0)
    env.tg.sent.clear()
    env.web.plays["p"] = FetchError("HTTP 503: blocked")
    _burst_run(env, t0 + timedelta(seconds=20), 100)
    _burst_run(env, t0 + timedelta(minutes=3), 100)
    failing = [m for m in env.tg.sent if "taraması başarısız" in m]
    assert len(failing) == 1
