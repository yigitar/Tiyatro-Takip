from __future__ import annotations

import copy
import hashlib
import pathlib
from datetime import datetime, timedelta

import pytest

import tiyatro.runner as runner
from tests.conftest import FakeStore
from tiyatro.claims import ClaimStore, claim_path
from tiyatro.config import DEFAULT_SOURCE
from tiyatro.fetcher import FetchError, FetchResult
from tiyatro.models import ISTANBUL, Event, Play, Session, Status
from tiyatro.notify import TelegramError
from tiyatro.parser import ParseError, PlayRef
from tiyatro.runner import (burst_loop, daily_due, pc_watch, plan_mode, run_once,
                            event_from_dict, event_to_dict)
from tiyatro.config import config_from_dict
from tiyatro.state import empty_state

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=ISTANBUL)  # Friday
SAT_20 = datetime(2026, 11, 21, 20, 0, tzinfo=ISTANBUL)
OPENS = datetime(2026, 11, 16, 10, 0, tzinfo=ISTANBUL)
BASE = "https://biletinial.com/tr-tr/tiyatro/"
FIXTURES = pathlib.Path(__file__).parent / "fixtures"

WATCH_CFG = {"plays": {"p": {"active": True, "targets": [
    {"id": "t1", "name": "Aksam", "people": 2,
     "rule": {"days": [5, 6], "from_time": "18:00"}}]}}}


# ---- fakes ---------------------------------------------------------------

class FakeTG:
    def __init__(self):
        self.sent: list[str] = []
        self.fail = False

    def send(self, text: str) -> int:
        if self.fail:
            raise TelegramError("telegram transport error: URLError")
        self.sent.append(text)
        return len(self.sent)


def sess(sid="s1", status=Status.UPCOMING, start=SAT_20, opens_at=OPENS):
    return Session(sid, start, None, status, opens_at if status is Status.UPCOMING else None,
                   f"{BASE}x/{sid}", "Sahne")


def mkplay(slug="p", sessions=None, title=None):
    return Play(slug, title or f"Oyun {slug}", BASE + slug, "Dram", 90, None, 100.0,
                list(sessions if sessions is not None else [sess()]))


class Web:
    """Fake site: the venue lists ``venue`` slugs; ``plays`` maps slug -> Play | Exception."""

    def __init__(self, monkeypatch, plays=(), venue=None):
        self.plays = {p.slug: p for p in plays}
        self.venue = list(venue) if venue is not None else [p.slug for p in plays]
        self.venue_error: Exception | None = None
        self.calls: list[str] = []
        monkeypatch.setattr(runner, "parse_venue", self._parse_venue)
        monkeypatch.setattr(runner, "parse_play", self._parse_play)

    def set(self, play):
        self.plays[play.slug] = play

    def fetch(self, url):
        self.calls.append(url)
        if url == DEFAULT_SOURCE:
            if self.venue_error is not None:
                raise self.venue_error
            return FetchResult("VENUE", 200, 1)
        slug = url.rsplit("/", 1)[-1]
        val = self.plays.get(slug)
        if isinstance(val, FetchError) or val is None:
            raise FetchError(f"HTTP 404: {url}")
        return FetchResult("PLAY:" + slug, 200, 1)

    def _parse_venue(self, html, base="https://biletinial.com"):
        assert html == "VENUE"
        return [PlayRef(s, s, BASE + s) for s in self.venue]

    def _parse_play(self, html, url):
        val = self.plays[html.split(":", 1)[1]]
        if isinstance(val, Exception):
            raise val
        return val


class Env:
    def __init__(self, monkeypatch, plays=(), config=None, who="github", store=None):
        self.store = store or FakeStore()
        if config is not None:
            self.store.files["config.json"] = (config, "cfg")
        self.web = Web(monkeypatch, plays)
        self.tg = FakeTG()
        self.who = who
        self.claims = ClaimStore(self.store, who, lambda: NOW)
        self.sleeps: list[float] = []

    def run(self, now, **kw):
        self.store.now = now
        kw.setdefault("who", self.who)
        return run_once(self.store, self.tg, self.claims, now, fetch_fn=self.web.fetch,
                        sleep=self.sleeps.append, **kw)

    def saved_state(self):
        return self.store.files["state.json"][0]


def on_sale_ready(monkeypatch):
    """Env with a saved first scan of a watched UPCOMING session."""
    env = Env(monkeypatch, [mkplay()], WATCH_CFG)
    env.run(NOW)
    env.tg.sent.clear()
    env.web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))
    return env


# ---- (a) venue failure ---------------------------------------------------

def test_venue_failure_streak_and_single_fetch_failing(monkeypatch):
    env = Env(monkeypatch, [mkplay("a")])
    env.run(NOW)  # healthy first scan
    env.tg.sent.clear()
    env.web.venue = []  # venue lists 0 plays
    calls = []
    real_diff = runner.diff
    monkeypatch.setattr(runner, "diff", lambda *a, **k: (calls.append(1), real_diff(*a, **k))[1])

    kinds = []
    for i in range(1, 5):
        res = env.run(NOW + timedelta(minutes=5 * i))
        assert env.saved_state()["meta"]["fetch_fail_streak"] == i
        kinds.append([e.kind for e in res.events])
    assert calls == []
    assert kinds == [[], [], ["FETCH_FAILING"], []]
    assert len(env.tg.sent) == 1 and "taraması başarısız" in env.tg.sent[0]
    st = env.saved_state()
    assert st["plays"]["a"]["missing_count"] == 0 and not st["plays"]["a"]["removed"]
    key = [k for k in st["emitted"] if k.startswith("FETCH_FAILING:")]
    assert key == [f"FETCH_FAILING:{(NOW + timedelta(minutes=5)).isoformat()}"]

    env.web.venue = ["a"]
    env.run(NOW + timedelta(minutes=30))
    assert env.saved_state()["meta"]["fetch_fail_streak"] == 0
    assert calls == [1]


def test_venue_fetch_error_counts_as_failure(monkeypatch):
    env = Env(monkeypatch, [mkplay("a")])
    env.web.venue_error = FetchError("HTTP 503: x")
    res = env.run(NOW)
    assert res.failures == 1 and res.scanned == 0
    assert env.saved_state()["meta"]["fetch_fail_streak"] == 1
    assert env.tg.sent == []


# ---- (b) ON_SALE -----------------------------------------------------------

def test_upcoming_to_on_sale_sends_exactly_once(monkeypatch):
    env = on_sale_ready(monkeypatch)
    res = env.run(NOW + timedelta(minutes=5))
    assert [e.kind for e in res.events] == ["ON_SALE"]
    assert len(env.tg.sent) == 1 and "Bilet alınabilir" in env.tg.sent[0]
    assert "Aksam (2 kişi)" in env.tg.sent[0]
    env.run(NOW + timedelta(minutes=10))
    assert len(env.tg.sent) == 1
    assert env.saved_state()["outbox"] == []
    claim = env.store.files[claim_path("ON_SALE:s1")][0]  # one claim per session
    assert claim["status"] == "sent" and claim["message_id"] == 1


def test_normal_scan_sleeps_between_requests(monkeypatch):
    env = Env(monkeypatch, [mkplay("a"), mkplay("b"), mkplay("c")])
    env.run(NOW)
    assert len(env.web.calls) == 4  # venue + 3 plays
    assert len(env.sleeps) == 3 and all(s >= 2 for s in env.sleeps)


# ---- (c) paused ------------------------------------------------------------

def test_paused_updates_state_but_sends_nothing(monkeypatch):
    cfg = {**WATCH_CFG, "paused": True}
    env = Env(monkeypatch, [mkplay()], cfg)
    env.run(NOW)
    env.web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))
    res = env.run(NOW + timedelta(minutes=5))
    assert [e.kind for e in res.events] == ["ON_SALE"]
    st = env.saved_state()
    assert st["plays"]["p"]["sessions"]["s1"]["status"] == "on_sale"
    assert "ON_SALE:s1:1" in st["emitted"] and st["outbox"] == []
    assert st["meta"]["paused_discarded"] == 2  # NEW_PLAY + ON_SALE
    assert env.tg.sent == []

    env.store.files["config.json"] = (WATCH_CFG, "cfg2")  # unpause
    env.run(NOW + timedelta(minutes=10))
    assert env.tg.sent == []


# ---- (d) plan_mode -----------------------------------------------------------

def _state_with_opening(opens_at, slug="p", status="upcoming"):
    return {"plays": {slug: {"title": "Oyun", "url": BASE + slug, "removed": False,
                             "first_seen": NOW.isoformat(),
                             "sessions": {"s1": {"status": status, "start": SAT_20.isoformat(),
                                                 "opens_at": opens_at.isoformat(),
                                                 "url": "u", "gone": False}}}},
            "meta": {"last_scan": None, "fetch_fail_streak": 0}, "emitted": {}}


@pytest.mark.parametrize("offset_min,mode", [(-11, "normal"), (-9, "burst"),
                                              (11, "burst"), (30, "burst"),
                                              (91, "burst"), (180, "burst"),
                                              (240, "burst"), (241, "normal")])
def test_plan_mode_window(offset_min, mode):
    st = _state_with_opening(OPENS)  # still UPCOMING: the site has not flipped yet
    cfg = config_from_dict(WATCH_CFG)
    got, until = plan_mode(st, cfg, OPENS + timedelta(minutes=offset_min))
    assert got == mode
    assert until == (OPENS + timedelta(hours=4) if mode == "burst" else None)


def test_plan_mode_recent_flip_to_on_sale():
    st = _state_with_opening(OPENS, status="on_sale")
    flip = OPENS + timedelta(minutes=25)
    st["plays"]["p"]["sessions"]["s1"]["status_since"] = flip.isoformat()
    cfg = config_from_dict(WATCH_CFG)
    assert plan_mode(st, cfg, flip + timedelta(minutes=9)) == (
        "burst", flip + timedelta(minutes=10))
    assert plan_mode(st, cfg, flip + timedelta(minutes=11)) == ("normal", None)
    # an ON_SALE session without a recent flip does not keep bursting
    del st["plays"]["p"]["sessions"]["s1"]["status_since"]
    assert plan_mode(st, cfg, flip)[0] == "normal"


def test_plan_mode_ignores_unwatched_and_unmatched():
    st = _state_with_opening(OPENS)
    assert plan_mode(st, config_from_dict({}), OPENS)[0] == "normal"
    weekday_only = {"plays": {"p": {"targets": [
        {"id": "t", "name": "n", "people": 1, "rule": {"days": [0]}}]}}}
    assert plan_mode(st, config_from_dict(weekday_only), OPENS)[0] == "normal"


# ---- burst loop --------------------------------------------------------------

def test_burst_loop_scans_only_relevant_play_until_end(monkeypatch):
    opens = NOW + timedelta(minutes=5)
    p = mkplay(sessions=[sess(opens_at=opens)])
    env = Env(monkeypatch, [p, mkplay("other")], WATCH_CFG)
    env.run(NOW)
    env.tg.sent.clear()
    env.web.calls.clear()
    writes = []
    real = env.store.update_json
    monkeypatch.setattr(env.store, "update_json",
                        lambda path, m, retries=4: (writes.append(path), real(path, m, retries))[1])
    clock = [NOW]

    def sleep(s):
        clock[0] += timedelta(seconds=s)
        if clock[0] >= opens and env.web.plays["p"].sessions[0].status is Status.UPCOMING:
            env.web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))

    until = opens + timedelta(minutes=10)
    res = burst_loop(env.store, env.tg, env.claims, lambda: clock[0], sleep, until,
                     fetch_fn=env.web.fetch)
    assert set(env.web.calls) == {BASE + "p"}
    assert DEFAULT_SOURCE not in env.web.calls
    # stops when the session leaves the burst window (opens_at cleared by ON_SALE)
    # or at `until`, never after max_seconds
    assert clock[0] <= NOW + timedelta(seconds=runner.BURST_MAX_SECONDS + 18)
    # the in-burst pc_watch adds one PC_INACTIVE (no heartbeat in this fake store)
    assert sum("Bilet alınabilir" in m for m in env.tg.sent) == 1
    assert sum("PC uygulaması aktif değil" in m for m in env.tg.sent) == 1
    assert res.state["plays"]["p"]["sessions"]["s1"]["status"] == "on_sale"
    # persisted only when something changed (not every 12 s iteration)
    n_iter = len(env.web.calls)
    assert 1 <= writes.count("state.json") < n_iter


def test_burst_loop_respects_until_and_max_seconds(monkeypatch):
    opens = NOW + timedelta(minutes=5)
    env = Env(monkeypatch, [mkplay(sessions=[sess(opens_at=opens)])], WATCH_CFG)
    env.run(NOW)
    clock = [NOW]

    def sleep(s):
        clock[0] += timedelta(seconds=s)

    burst_loop(env.store, env.tg, env.claims, lambda: clock[0], sleep,
               opens + timedelta(minutes=10), fetch_fn=env.web.fetch, max_seconds=60)
    assert clock[0] - NOW <= timedelta(seconds=60 + 18)
    clock[0] = NOW
    burst_loop(env.store, env.tg, env.claims, lambda: clock[0], sleep,
               NOW + timedelta(seconds=30), fetch_fn=env.web.fetch)
    assert clock[0] - NOW <= timedelta(seconds=30 + 18)


# ---- (e) pc_watch ------------------------------------------------------------

class RunsStore(FakeStore):
    def __init__(self, runs_age_min=2):
        super().__init__()
        self.runs_age_min = runs_age_min

    def runs(self, per_page=10):
        t = (self.now - timedelta(minutes=self.runs_age_min)).isoformat()
        return [{"created_at": t, "status": "completed", "conclusion": "success",
                 "path": ".github/workflows/scan.yml", "id": 1}]


def _pc_env(store, opens):
    store.now = NOW
    st = _state_with_opening(opens)
    return st, config_from_dict(WATCH_CFG), FakeTG(), ClaimStore(store, "github", lambda: NOW)


def test_pc_watch_inactive_twice(monkeypatch):
    opens = NOW + timedelta(minutes=59)
    store = RunsStore()
    st, cfg, tg, claims = _pc_env(store, opens)
    ev = pc_watch(store, st, cfg, NOW, claims, tg)
    assert [e.key for e in ev] == [f"PC_INACTIVE:{opens.isoformat()}:1"]
    assert len(tg.sent) == 1 and "PC uygulaması aktif değil" in tg.sent[0]
    assert pc_watch(store, st, cfg, NOW + timedelta(minutes=5), claims, tg) == []
    assert len(tg.sent) == 1
    store.now = opens - timedelta(minutes=9)  # GitHub runs stay fresh
    ev = pc_watch(store, st, cfg, opens - timedelta(minutes=9), claims, tg)
    assert [e.key for e in ev] == [f"PC_INACTIVE:{opens.isoformat()}:2"]
    assert len(tg.sent) == 2


def test_pc_watch_outside_window_does_nothing():
    store = RunsStore(runs_age_min=30)
    st, cfg, tg, claims = _pc_env(store, NOW + timedelta(minutes=61))
    assert pc_watch(store, st, cfg, NOW, claims, tg) == []
    assert tg.sent == []


def test_pc_watch_active_and_diverged():
    opens = NOW + timedelta(minutes=30)
    store = RunsStore()
    st, cfg, tg, claims = _pc_env(store, opens)
    store.files["heartbeat.json"] = ({"ts": NOW.isoformat(), "observed": {"s1": "upcoming"}},
                                     "h1")
    assert pc_watch(store, st, cfg, NOW, claims, tg) == []
    store.files["heartbeat.json"] = ({"ts": NOW.isoformat(), "observed": {"s1": "on_sale"}},
                                     "h2")
    ev = pc_watch(store, st, cfg, NOW, claims, tg)
    assert [e.key for e in ev] == [f"PC_DIVERGED:{opens.isoformat()}"]
    assert ev[0].payload["detail"].startswith("PC farklı görüyor")
    assert len(tg.sent) == 1 and "PC farklı görüyor" in tg.sent[0]
    assert pc_watch(store, st, cfg, NOW, claims, tg) == []


def test_pc_watch_gh_lagging():
    opens = NOW + timedelta(minutes=30)
    store = RunsStore(runs_age_min=20)
    st, cfg, tg, claims = _pc_env(store, opens)
    store.files["heartbeat.json"] = ({"ts": NOW.isoformat(), "observed": {}}, "h1")
    ev = pc_watch(store, st, cfg, NOW, claims, tg)
    assert [e.key for e in ev] == [f"GH_LAGGING:{opens.isoformat()}"]
    assert "GitHub taraması gecikiyor" in tg.sent[0]


def test_pc_watch_store_errors_fail_open():
    opens = NOW + timedelta(minutes=30)
    store = FakeStore()  # no runs(); heartbeat read fails below
    st, cfg, tg, claims = _pc_env(store, opens)

    def boom(path):
        raise RuntimeError("down")

    store.get = boom
    ev = pc_watch(store, st, cfg, NOW, claims, tg)
    assert [e.kind for e in ev] == ["PC_INACTIVE"]
    assert len(tg.sent) == 1


# ---- (f) daily ---------------------------------------------------------------

def test_daily_due():
    st = empty_state()
    day = datetime(2026, 11, 13, tzinfo=ISTANBUL)
    assert daily_due(st, day.replace(hour=11, minute=59)) is False
    assert daily_due(st, day.replace(hour=12, minute=1)) is True
    st["meta"]["last_daily"] = "2026-11-13"
    assert daily_due(st, day.replace(hour=12, minute=5)) is False
    assert daily_due(st, day.replace(hour=12, minute=5) + timedelta(days=1)) is True


def test_run_once_marks_daily(monkeypatch):
    env = Env(monkeypatch, [mkplay("a")])
    res = env.run(NOW.replace(hour=11, minute=59))
    assert res.daily is False and env.saved_state()["meta"].get("last_daily") is None
    res = env.run(NOW.replace(hour=12, minute=1))
    assert res.daily is True and env.saved_state()["meta"]["last_daily"] == "2026-11-13"
    assert env.run(NOW.replace(hour=12, minute=6)).daily is False


# ---- (g) per-play failure ---------------------------------------------------

def test_play_parse_error_does_not_abort_scan(monkeypatch):
    env = Env(monkeypatch, [mkplay("a"), mkplay("b"), mkplay("c")])
    env.run(NOW)
    before = env.saved_state()["plays"]["b"]
    env.web.plays["b"] = ParseError("rows vs json-ld")
    for i in range(1, 4):
        res = env.run(NOW + timedelta(minutes=5 * i))
        assert res.failures == 1 and res.scanned == 2
        assert [e.kind for e in res.events] == []
    st = env.saved_state()
    after = {k: v for k, v in st["plays"]["b"].items()
             if k not in ("fail_streak", "fail_since")}
    before = {k: v for k, v in before.items() if k not in ("fail_streak", "fail_since")}
    assert after == before and st["plays"]["b"]["fail_streak"] == 3
    assert st["meta"]["fetch_fail_streak"] == 0
    env.web.plays["b"] = FetchError("HTTP 500")
    assert env.run(NOW + timedelta(minutes=30)).failures == 1
    assert not env.saved_state()["plays"]["b"]["removed"]


def test_unexpected_parser_exception_is_per_play(monkeypatch):
    env = Env(monkeypatch, [mkplay("a"), mkplay("b")])
    env.web.plays["a"] = KeyError("bug")
    res = env.run(NOW)
    assert res.failures == 1 and res.scanned == 1


# ---- (h) outbox ----------------------------------------------------------------

def test_telegram_failure_keeps_event_in_outbox_then_delivers_once(monkeypatch):
    env = on_sale_ready(monkeypatch)
    env.tg.fail = True
    env.run(NOW + timedelta(minutes=5))
    box = env.saved_state()["outbox"]
    assert [e["key"] for e in box] == ["ON_SALE:s1:1"] and box[0]["attempts"] == 1
    env.tg.fail = False
    env.claims.clock = lambda: NOW + timedelta(minutes=10)
    env.run(NOW + timedelta(minutes=10))
    assert len(env.tg.sent) == 1 and "Bilet alınabilir" in env.tg.sent[0]
    assert env.saved_state()["outbox"] == []
    env.run(NOW + timedelta(minutes=15))
    assert len(env.tg.sent) == 1


def test_failed_send_backs_off(monkeypatch):
    env = on_sale_ready(monkeypatch)
    env.tg.fail = True
    t = NOW + timedelta(minutes=5)
    env.run(t)
    box = env.saved_state()["outbox"]
    assert box[0]["not_before"] == (t + timedelta(seconds=10)).isoformat()
    env.tg.fail = False
    env.run(t + timedelta(seconds=5), only_slugs=["p"])  # burst tick before back-off ends
    assert env.tg.sent == [] and env.saved_state()["outbox"][0]["attempts"] == 1
    env.run(t + timedelta(seconds=12), only_slugs=["p"])
    assert len(env.tg.sent) == 1 and env.saved_state()["outbox"] == []


def test_outbox_dropped_when_other_side_already_sent(monkeypatch):
    env = on_sale_ready(monkeypatch)
    env.tg.fail = True
    env.run(NOW + timedelta(minutes=5))
    path = claim_path("ON_SALE:s1")
    rec, sha = env.store.get(path)
    env.store.put(path, {**rec, "status": "sent", "claimed_by": "pc",
                         "sent_at": (NOW + timedelta(minutes=6)).isoformat()}, sha)
    env.tg.fail = False
    env.run(NOW + timedelta(minutes=10))
    assert env.tg.sent == []
    assert env.saved_state()["outbox"] == []


def test_outbox_expires_after_6h_and_is_logged(monkeypatch):
    env = on_sale_ready(monkeypatch)
    env.tg.fail = True
    env.run(NOW + timedelta(minutes=5))
    res = env.run(NOW + timedelta(hours=6, minutes=6))
    st = env.saved_state()
    assert [e["key"] for e in st["outbox"]] == ["DROPPED:ON_SALE:s1:1"]
    assert res.failures >= 1
    assert st["meta"]["outbox_dropped"][-1]["key"] == "ON_SALE:s1:1"


def test_outbox_drops_after_20_attempts(monkeypatch):
    env = on_sale_ready(monkeypatch)
    env.tg.fail = True
    seen = []
    for i in range(25):  # every 10 min: the retry back-off (max 10 min) has elapsed
        t = NOW + timedelta(minutes=5 + 10 * i)
        env.claims.clock = lambda t=t: t
        env.run(t)
        seen.append([e["attempts"] for e in env.saved_state()["outbox"]
                     if e["key"] == "ON_SALE:s1:1"])
    # the 20th failed attempt drops the entry in the same run
    assert seen[0] == [1] and seen[18] == [19] and seen[19] == [], seen
    st = env.saved_state()
    assert [e["key"] for e in st["outbox"]] == ["DROPPED:ON_SALE:s1:1"]
    assert st["meta"]["outbox_dropped"][-1]["reason"] == "attempts"
    assert st["meta"]["outbox_dropped"][-1]["attempts"] == 20
    assert len(st["meta"]["outbox_dropped"]) == 1


def test_multipart_message_all_parts_then_sent(monkeypatch):
    env = Env(monkeypatch, [mkplay()], WATCH_CFG)
    env.run(NOW)
    st = env.saved_state()
    ev = Event("ON_SALE", "ON_SALE:big:1", "p", "s1", ["t1"], {"title": "T"})
    monkeypatch.setattr(runner, "format_message", lambda evs, plays, cfg: ["part1", "part2"])
    st["outbox"] = [{"key": ev.key, "event": event_to_dict(ev), "created": NOW.isoformat(),
                     "attempts": 0, "not_before": NOW.isoformat()}]
    env.run(NOW + timedelta(minutes=1), state=st)
    assert env.tg.sent[-2:] == ["part1", "part2"]
    claim = env.store.files[claim_path("ON_SALE:big")][0]
    assert claim["status"] == "sent" and claim["message_id"] == len(env.tg.sent)


def test_event_dict_roundtrip():
    ev = Event("ON_SALE", "ON_SALE:s:1", "p", "s", ["t1"], {"a": 1})
    assert event_from_dict(event_to_dict(ev)) == ev


# ---- (i) setup batch -----------------------------------------------------------

def test_first_scan_sends_one_folded_setup_message(monkeypatch):
    env = Env(monkeypatch, [mkplay(f"p{i}") for i in range(6)])
    res = env.run(NOW)
    assert len([e for e in res.events if e.kind == "NEW_PLAY"]) == 6
    assert len(env.tg.sent) == 1 and "(6)" in env.tg.sent[0]
    key = runner.setup_key([e for e in res.events if e.kind == "NEW_PLAY"])
    assert env.store.files[claim_path(key)][0]["status"] == "sent"
    # a later new play is announced on its own
    env.web.set(mkplay("p9"))
    env.web.venue.append("p9")
    env.run(NOW + timedelta(minutes=5))
    assert len(env.tg.sent) == 2 and "Yeni oyun" in env.tg.sent[1]


def test_first_scan_with_real_fixtures(monkeypatch):
    files = {"on-ikinci-gece-kocaeli-bb": "play_on_ikinci_gece.html",
             "hic-kimsenin-oykusu-kocaeli-buyuksehir-sehir-tiyatrolari":
                 "play_hic_kimsenin.html",
             "kusursuz-dunyaya-yolculuk-kocaeli-bb": "play_kusursuz.html"}

    def fetch_fn(url):
        if url == DEFAULT_SOURCE:
            name = "venue.html"
        else:
            name = files.get(url.rsplit("/", 1)[-1])
            if name is None:
                raise FetchError(f"HTTP 404: {url}")
        return FetchResult((FIXTURES / name).read_text(encoding="utf-8"), 200, 1)

    store, tg = FakeStore(), FakeTG()
    res = run_once(store, tg, ClaimStore(store, "github", lambda: NOW), NOW,
                   fetch_fn=fetch_fn, sleep=lambda s: None)
    # 3 of 6 play pages unavailable: per-play failures + one scan failure (C1)
    assert res.scanned == 3 and res.failures == 4
    assert store.files["state.json"][0]["meta"]["fetch_fail_streak"] == 1
    assert len(tg.sent) == 1
    assert set(store.files["state.json"][0]["plays"]) == set(files)


# ---- (j) only_slugs ----------------------------------------------------------

def test_only_slugs_never_removes_unscanned_plays(monkeypatch):
    env = Env(monkeypatch, [mkplay("a"), mkplay("b")])
    env.run(NOW)
    env.web.calls.clear()
    for i in range(1, 4):
        res = env.run(NOW + timedelta(minutes=i), only_slugs=["a"])
        assert res.mode == "burst" and res.scanned == 1
        assert [e.kind for e in res.events] == []
    assert env.web.calls == [BASE + "a"] * 3
    assert env.sleeps.count(2) == 2  # only the first (normal) scan slept
    st = env.saved_state()
    assert st["plays"]["b"]["missing_count"] == 0 and not st["plays"]["b"]["removed"]
    assert st["meta"]["fetch_fail_streak"] == 0


# ---- (k) PC runner -------------------------------------------------------------

def test_pc_runner_no_state_write_and_delayed_send(monkeypatch):
    gh = on_sale_ready(monkeypatch)
    saved = gh.store.files["state.json"]
    pc_claims = ClaimStore(gh.store, "pc", lambda: NOW)
    pc_tg = FakeTG()
    t = NOW + timedelta(minutes=5)
    res = run_once(gh.store, pc_tg, pc_claims, t, who="pc", persist_state=False,
                   fetch_fn=gh.web.fetch, sleep=lambda s: None)
    assert gh.store.files["state.json"] is saved  # untouched
    assert [e.kind for e in res.events] == ["ON_SALE"]
    assert pc_tg.sent == []
    box = res.state["outbox"]
    assert box[0]["not_before"] == (t + timedelta(seconds=15)).isoformat()

    t2 = t + timedelta(seconds=16)
    gh.store.now = t2  # server clock (claim sent_at)
    res2 = run_once(gh.store, pc_tg, pc_claims, t2, who="pc", persist_state=False,
                    state=res.state, fetch_fn=gh.web.fetch, sleep=lambda s: None)
    assert len(pc_tg.sent) == 1 and res2.state["outbox"] == []
    assert gh.store.files["state.json"] is saved
    # GitHub now sees the claim as sent and does not duplicate
    gh.run(t2 + timedelta(seconds=30))
    assert gh.tg.sent == []


def test_pc_defers_to_github_when_github_sent_first(monkeypatch):
    gh = on_sale_ready(monkeypatch)
    pc_claims = ClaimStore(gh.store, "pc", lambda: NOW)
    pc_tg = FakeTG()
    t = NOW + timedelta(minutes=5)
    res = run_once(gh.store, pc_tg, pc_claims, t, who="pc", persist_state=False,
                   fetch_fn=gh.web.fetch, sleep=lambda s: None)
    gh.run(t + timedelta(seconds=20))
    assert len(gh.tg.sent) == 1
    run_once(gh.store, pc_tg, pc_claims, t + timedelta(seconds=70), who="pc",
             persist_state=False, state=res.state, fetch_fn=gh.web.fetch,
             sleep=lambda s: None)
    assert pc_tg.sent == []


# ---- (l) save failure ----------------------------------------------------------

class FlakyStateStore(FakeStore):
    fail_state = False

    def update_json(self, path, mutate, retries=4):
        if self.fail_state and path == "state.json":
            raise RuntimeError("store down")
        return super().update_json(path, mutate, retries)

    def put(self, path, data, sha):
        if self.fail_state and path == "state.json":
            raise RuntimeError("store down")
        return super().put(path, data, sha)


def test_save_failure_after_send_does_not_resend(monkeypatch):
    store = FlakyStateStore()
    env = Env(monkeypatch, [mkplay()], WATCH_CFG, store=store)
    env.run(NOW)
    env.tg.sent.clear()
    env.web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))
    store.fail_state = True
    res = env.run(NOW + timedelta(minutes=5))
    assert len(env.tg.sent) == 1 and res.save_error
    store.fail_state = False
    # next (burst) run within the 3-min ON_SALE gap; a later re-derive may duplicate
    res = env.run(NOW + timedelta(minutes=7))
    assert [e.kind for e in res.events] == ["ON_SALE"]  # re-derived from the old state
    assert len(env.tg.sent) == 1  # but the claim says sent: no duplicate
    assert env.saved_state()["outbox"] == [] and res.save_error is None


# ---- main ----------------------------------------------------------------------

def test_main_missing_env_returns_nonzero(monkeypatch, capsys):
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GITHUB_TOKEN", "GITHUB_REPOSITORY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_SECRET")
    assert runner.main() != 0
    err = capsys.readouterr().err
    assert "TELEGRAM_BOT_TOKEN" in err and "ghp_SECRET" not in err


def test_main_end_to_end_and_secret_free_errors(monkeypatch, capsys):
    env = {"TELEGRAM_BOT_TOKEN": "123:TGSECRET", "TELEGRAM_CHAT_ID": "42",
           "GITHUB_TOKEN": "ghp_SECRET", "GITHUB_REPOSITORY": "o/r"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    class MainStore(RunsStore):
        def __init__(self, repo, token, branch="data", transport=None):
            super().__init__()
            assert (repo, token) == ("o/r", "ghp_SECRET")
            self.branch_ensured = False
            made.append(self)

        def ensure_branch(self):
            self.branch_ensured = True

    made, tgs = [], []

    def make_tg(token, chat_id, transport=None, sleep=None):
        assert (token, chat_id) == ("123:TGSECRET", "42")
        tgs.append(FakeTG())
        return tgs[-1]

    web = Web(monkeypatch, [mkplay("a")])
    monkeypatch.setattr(runner, "Store", MainStore)
    monkeypatch.setattr(runner, "Telegram", make_tg)
    monkeypatch.setattr(runner, "fetch", web.fetch)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    assert runner.main() == 0
    assert made[0].branch_ensured and "state.json" in made[0].files
    assert len(tgs[0].sent) == 1

    def explode(self):
        raise RuntimeError("bad token ghp_SECRET 123:TGSECRET")

    monkeypatch.setattr(MainStore, "ensure_branch", explode)
    assert runner.main() != 0
    err = capsys.readouterr().err
    assert "ghp_SECRET" not in err and "TGSECRET" not in err and "RuntimeError" in err


# ==== fix round 1 =================================================================

# ---- C1: play-page parser break must not be silent ---------------------------------

def test_all_play_pages_failing_is_a_scan_failure(monkeypatch):
    env = Env(monkeypatch, [mkplay("a"), mkplay("b"), mkplay("c")], WATCH_CFG)
    env.run(NOW)
    env.tg.sent.clear()
    for slug in ("a", "b", "c"):
        env.web.plays[slug] = ParseError("layout changed")
    kinds = []
    for i in range(1, 6):
        res = env.run(NOW + timedelta(minutes=5 * i))
        kinds.append([e.kind for e in res.events])
    assert env.saved_state()["meta"]["fetch_fail_streak"] == 5
    assert kinds == [[], [], ["FETCH_FAILING"], [], []]
    assert len(env.tg.sent) == 1
    st = env.saved_state()
    assert not any(st["plays"][s]["removed"] for s in ("a", "b", "c"))


def test_half_of_play_pages_failing_is_a_scan_failure(monkeypatch):
    env = Env(monkeypatch, [mkplay(s) for s in "abcd"])
    env.run(NOW)
    env.web.plays["a"] = ParseError("x")
    env.web.plays["b"] = FetchError("y")
    res = env.run(NOW + timedelta(minutes=5))
    assert env.saved_state()["meta"]["fetch_fail_streak"] == 1 and res.scanned == 2


def test_watched_play_failing_three_runs_emits_one_play_failing(monkeypatch):
    plays = [mkplay("p"), mkplay("a"), mkplay("b"), mkplay("c")]
    env = Env(monkeypatch, plays, WATCH_CFG)
    env.run(NOW)
    env.tg.sent.clear()
    env.web.plays["p"] = ParseError("rows vs json-ld")
    kinds = []
    for i in range(1, 6):
        res = env.run(NOW + timedelta(minutes=5 * i))
        kinds.append([e.kind for e in res.events])
    assert kinds == [[], [], ["PLAY_FAILING"], [], []]
    st = env.saved_state()
    assert st["plays"]["p"]["fail_streak"] == 5
    assert st["meta"]["fetch_fail_streak"] == 0
    key = [k for k in st["emitted"] if k.startswith("PLAY_FAILING:")]
    assert key == [f"PLAY_FAILING:p:{(NOW + timedelta(minutes=5)).isoformat()}"]
    assert len(env.tg.sent) == 1 and "Oyun sayfası okunamıyor: Oyun p" in env.tg.sent[0]
    env.web.set(mkplay("p"))
    env.run(NOW + timedelta(minutes=40))
    assert env.saved_state()["plays"]["p"]["fail_streak"] == 0


def test_unwatched_play_failing_is_not_alerted(monkeypatch):
    env = Env(monkeypatch, [mkplay(s) for s in "pabc"], WATCH_CFG)
    env.run(NOW)
    env.web.plays["a"] = ParseError("x")
    for i in range(1, 5):
        assert env.run(NOW + timedelta(minutes=5 * i)).events == []


def test_single_transient_play_failure_is_silent(monkeypatch):
    env = Env(monkeypatch, [mkplay(s) for s in "pabc"], WATCH_CFG)
    env.run(NOW)
    env.tg.sent.clear()
    env.web.plays["p"] = FetchError("HTTP 502")
    env.run(NOW + timedelta(minutes=5))
    env.web.set(mkplay("p"))
    for i in range(2, 6):
        env.run(NOW + timedelta(minutes=5 * i))
    assert env.tg.sent == []
    st = env.saved_state()
    assert st["plays"]["p"]["fail_streak"] == 0 and st["meta"]["fetch_fail_streak"] == 0


# ---- I1: GH_LAGGING ignores the current run -----------------------------------------

SCAN_PATH = ".github/workflows/scan.yml"


class HeadRunStore(FakeStore):
    """runs(): the current in-progress run first, then the given completed runs."""

    def __init__(self, previous):
        super().__init__()
        self.previous = previous

    def runs(self, per_page=10):
        cur = {"id": 999, "path": SCAN_PATH, "status": "in_progress", "conclusion": None,
               "created_at": self.now.isoformat()}
        other = {"id": 5, "path": ".github/workflows/test.yml", "status": "completed",
                 "conclusion": "success", "created_at": self.now.isoformat()}
        return [cur, other] + [
            {"id": 100 + i, "path": SCAN_PATH, "status": "completed", "conclusion": c,
             "created_at": (self.now - timedelta(minutes=m)).isoformat()}
            for i, (m, c) in enumerate(self.previous)]


def _gh_watch(monkeypatch, previous):
    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    store = HeadRunStore(previous)
    opens = NOW + timedelta(minutes=30)
    st, cfg, tg, claims = _pc_env(store, opens)
    store.files["heartbeat.json"] = ({"ts": NOW.isoformat(), "observed": {}}, "h1")
    return pc_watch(store, st, cfg, NOW, claims, tg), opens


def test_gh_lagging_with_in_progress_head_run(monkeypatch):
    ev, opens = _gh_watch(monkeypatch, [(40, "success"), (45, "success")])
    assert [e.key for e in ev] == [f"GH_LAGGING:{opens.isoformat()}"]


def test_gh_two_previous_failures_unhealthy(monkeypatch):
    ev, _ = _gh_watch(monkeypatch, [(5, "failure"), (10, "failure")])
    assert [e.kind for e in ev] == ["GH_LAGGING"]


def test_gh_healthy_previous_run_no_alert(monkeypatch):
    ev, _ = _gh_watch(monkeypatch, [(5, "success"), (10, "success")])
    assert ev == []


def test_scan_runs_filter():
    runs = [{"id": 1, "path": SCAN_PATH, "status": "in_progress"},
            {"id": 2, "path": SCAN_PATH, "status": "completed"},
            {"id": 3, "path": ".github/workflows/other.yml", "status": "completed"},
            {"id": 4, "name": "scan", "status": "completed"},
            "garbage"]
    assert [r["id"] for r in runner.scan_runs(runs, run_id="2")] == [4]
    assert [r["id"] for r in runner.scan_runs(runs, run_id=None)] == [2, 4]


# ---- I2: stable SETUP key ---------------------------------------------------------

def test_setup_key_stable_across_save_failure(monkeypatch):
    store = FlakyStateStore()
    env = Env(monkeypatch, [mkplay(f"p{i}") for i in range(6)], store=store)
    store.fail_state = True
    res = env.run(NOW)
    assert res.save_error and len(env.tg.sent) == 1
    store.fail_state = False
    env.run(NOW + timedelta(minutes=1))
    assert len(env.tg.sent) == 1
    digest = hashlib.sha1("\n".join(sorted(f"p{i}" for i in range(6))).encode()).hexdigest()
    assert claim_path(f"SETUP:{digest[:12]}") in store.files


# ---- I3: transient store errors in a burst ----------------------------------------

def test_burst_survives_one_store_error(monkeypatch):
    opens = NOW + timedelta(minutes=5)
    env = Env(monkeypatch, [mkplay(sessions=[sess(opens_at=opens)])], WATCH_CFG)
    env.run(NOW)
    env.tg.sent.clear()
    clock = [NOW]
    real_get = env.store.get
    config_gets = []

    def flaky_get(path):
        if path == "config.json":
            config_gets.append(1)
            if len(config_gets) == 2:
                raise RuntimeError("HTTP 502")
        return real_get(path)

    monkeypatch.setattr(env.store, "get", flaky_get)

    def sleep(s):
        clock[0] += timedelta(seconds=s)
        if clock[0] >= opens:
            env.web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))

    res = burst_loop(env.store, env.tg, env.claims, lambda: clock[0], sleep,
                     opens + timedelta(minutes=10), fetch_fn=env.web.fetch)
    assert len(config_gets) >= 3
    assert res.errors == ["RuntimeError"]
    assert sum("Bilet alınabilir" in m for m in env.tg.sent) == 1


def test_burst_sleeps_between_page_fetches(monkeypatch):
    opens = NOW + timedelta(minutes=5)
    two = [mkplay("p", sessions=[sess(opens_at=opens)]),
           mkplay("q", sessions=[sess("s2", opens_at=opens)])]
    cfg = {"plays": {"p": WATCH_CFG["plays"]["p"], "q": WATCH_CFG["plays"]["p"]}}
    env = Env(monkeypatch, two, cfg)
    env.run(NOW)
    env.sleeps.clear()
    env.run(NOW + timedelta(seconds=1), only_slugs=["p", "q"])
    assert env.sleeps and all(s >= 0.5 for s in env.sleeps)


def _main_env(monkeypatch):
    for k, v in {"TELEGRAM_BOT_TOKEN": "1:T", "TELEGRAM_CHAT_ID": "4",
                 "GITHUB_TOKEN": "ghp_X", "GITHUB_REPOSITORY": "o/r"}.items():
        monkeypatch.setenv(k, v)


def test_main_runs_pc_watch_after_burst_crash(monkeypatch, capsys):
    _main_env(monkeypatch)
    calls = []

    class S(RunsStore):
        def __init__(self, *a, **k):
            super().__init__()

        def ensure_branch(self):
            pass

    monkeypatch.setattr(runner, "Store", S)
    monkeypatch.setattr(runner, "Telegram", lambda *a, **k: FakeTG())
    monkeypatch.setattr(runner, "run_once", lambda *a, **k: runner.RunResult(
        [], "normal", 0, runner._prepare(empty_state()), daily=True))
    monkeypatch.setattr(runner, "plan_mode", lambda *a: ("burst", NOW))

    def boom(*a, **k):
        raise RuntimeError("burst died")

    monkeypatch.setattr(runner, "burst_loop", boom)
    monkeypatch.setattr(runner, "pc_watch", lambda *a, **k: calls.append("pc") or [])
    monkeypatch.setattr(runner.ClaimStore, "purge", lambda self, *a: calls.append("purge"))
    runner.main()
    assert calls == ["pc", "pc", "purge"]  # run-start check + post-burst check
    assert "RuntimeError" in capsys.readouterr().err


# ---- I4: daily new-session summary ----------------------------------------------

def test_daily_summary_of_new_sessions(monkeypatch):
    day = datetime(2026, 11, 13, 13, 0, tzinfo=ISTANBUL)
    env = Env(monkeypatch, [mkplay("a", title="Hamlet"), mkplay("b")])
    res = env.run(day)  # first daily: no previous daily -> nothing
    assert res.daily and [e.kind for e in res.events] == ["NEW_PLAY", "NEW_PLAY"]
    sat2 = SAT_20 + timedelta(days=7)
    env.web.set(mkplay("a", title="Hamlet", sessions=[
        sess(), sess("s7", start=sat2), sess("s8", start=sat2 + timedelta(days=7))]))
    res = env.run(day + timedelta(hours=2))
    assert res.events == []
    res = env.run(day + timedelta(days=1))
    assert [e.key for e in res.events] == ["NEW_SESSIONS:2026-11-14"]
    msg = env.tg.sent[-1]
    assert "Yeni seanslar/dönem" in msg and "Hamlet" in msg and "2 seans" in msg
    assert "28 Kasım" in msg and "5 Aralık" in msg
    res = env.run(day + timedelta(days=2))
    assert res.daily and res.events == []


# ---- I5: dropped alerts are announced -------------------------------------------

def test_drop_notice_delivered_once_after_outage(monkeypatch):
    env = on_sale_ready(monkeypatch)
    env.tg.fail = True
    env.run(NOW + timedelta(minutes=5))
    env.run(NOW + timedelta(hours=6, minutes=6))
    st = env.saved_state()
    assert [e["key"] for e in st["outbox"]] == ["DROPPED:ON_SALE:s1:1"]
    env.tg.fail = False
    env.run(NOW + timedelta(hours=6, minutes=30))
    env.run(NOW + timedelta(hours=7))
    assert len(env.tg.sent) == 1
    assert "1 bildirim iletilemedi" in env.tg.sent[0] and "ON_SALE" in env.tg.sent[0]
    assert env.saved_state()["outbox"] == []


def test_main_exits_nonzero_and_reports_drops(monkeypatch, capsys):
    _main_env(monkeypatch)
    st = runner._prepare(empty_state())
    st["outbox"] = [{"key": "ON_SALE:x:1", "event": event_to_dict(
        Event("ON_SALE", "ON_SALE:x:1", "p", "x", [], {})),
        "created": (datetime.now(ISTANBUL) - timedelta(hours=7)).isoformat(),
        "attempts": 3, "not_before": NOW.isoformat()}]

    class S(RunsStore):
        def __init__(self, *a, **k):
            super().__init__()
            self.files["state.json"] = (st, "s0")

        def ensure_branch(self):
            pass

    web = Web(monkeypatch, [mkplay("a")])
    monkeypatch.setattr(runner, "Store", S)
    monkeypatch.setattr(runner, "Telegram", lambda *a, **k: FakeTG())
    monkeypatch.setattr(runner, "fetch", web.fetch)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    assert runner.main() == 1
    assert "ON_SALE:x:1" in capsys.readouterr().err


# ---- M1: malformed state --------------------------------------------------------

@pytest.mark.parametrize("bad", [{"plays": None}, {"meta": None}, {"outbox": ["x"]},
                                 {"emitted": [1], "outbox": [{"no": "key"}, 5]},
                                 {"plays": {"a": "junk"}}, ["not", "a", "dict"]])
def test_malformed_state_is_coerced(monkeypatch, bad):
    env = Env(monkeypatch, [mkplay("a")])
    env.store.files["state.json"] = (bad, "s0")
    res = env.run(NOW)
    st = env.saved_state()
    assert isinstance(st["plays"], dict) and isinstance(st["meta"], dict)
    assert st["outbox"] == [] and res.scanned == 1


def test_store_error_on_state_load_propagates(monkeypatch):
    env = Env(monkeypatch, [mkplay("a")])
    env.store.files["state.json"] = ({"plays": {}, "meta": {"x": 1}}, "s0")
    real = env.store.get

    def get(path):
        if path == "state.json":
            raise RuntimeError("HTTP 502")
        return real(path)

    monkeypatch.setattr(env.store, "get", get)
    with pytest.raises(RuntimeError):
        env.run(NOW)
    assert env.store.files["state.json"][0] == {"plays": {}, "meta": {"x": 1}}


# ---- overdue openings: the site flips when the organiser enables sales ------------

def _overdue_env(monkeypatch, opens):
    env = Env(monkeypatch, [mkplay(sessions=[sess(opens_at=opens)])], WATCH_CFG)
    env.run(opens - timedelta(hours=2))
    env.tg.sent.clear()
    return env


def test_overdue_opening_flip_inside_burst_sends_once(monkeypatch):
    env = _overdue_env(monkeypatch, OPENS)
    start = OPENS + timedelta(minutes=20)  # still UPCOMING 20 min after opens_at
    assert plan_mode(env.saved_state(), config_from_dict(WATCH_CFG), start)[0] == "burst"
    clock = [start]
    ticks = []

    def sleep(s):
        if s >= 5:
            ticks.append(s)
        clock[0] += timedelta(seconds=s)
        if clock[0] >= OPENS + timedelta(minutes=25):
            env.web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))

    res = burst_loop(env.store, env.tg, env.claims, lambda: clock[0], sleep,
                     OPENS + timedelta(minutes=90), fetch_fn=env.web.fetch,
                     rand=lambda a, b: 0.0)
    assert len(env.tg.sent) == 1 and "Bilet alınabilir" in env.tg.sent[0]
    assert res.state["plays"]["p"]["sessions"]["s1"]["status"] == "on_sale"
    assert set(ticks) == {15}  # one cadence for the whole window
    assert clock[0] - start <= timedelta(seconds=runner.BURST_MAX_SECONDS + 15)
    # the next scheduled run re-enters burst cheaply and does not re-send
    res2 = burst_loop(env.store, env.tg, env.claims, lambda: clock[0], sleep,
                      OPENS + timedelta(minutes=90), fetch_fn=env.web.fetch,
                      rand=lambda a, b: 0.0)
    assert len(env.tg.sent) == 1 and res2.errors == []


@pytest.mark.parametrize("overdue_min", [-5, 5, 31, 95, 200])
def test_burst_polls_every_15_s_for_the_whole_overdue_window(monkeypatch, overdue_min):
    """Final review I2: no slower tiers; ~15 s (+-3 s jitter) up to opens_at + 4 h."""
    env = _overdue_env(monkeypatch, OPENS)
    clock = [OPENS + timedelta(minutes=overdue_min)]
    ticks = []

    def sleep(s):
        if s >= 5:
            ticks.append(s)
        clock[0] += timedelta(seconds=s)

    burst_loop(env.store, env.tg, env.claims, lambda: clock[0], sleep,
               OPENS + timedelta(hours=4), fetch_fn=env.web.fetch, max_seconds=180)
    assert ticks and all(12 <= t <= 18 for t in ticks)
    assert len(set(ticks)) > 1  # jittered


def test_burst_github_api_requests_per_hour_stay_under_600(monkeypatch):
    """Final review I2: worst-case jitter (every 12 s) for a whole hour of overdue burst
    must stay well under the ~1000/h GITHUB_TOKEN limit (estimate ~400/h)."""
    env = _overdue_env(monkeypatch, OPENS)
    calls = []
    for name in ("get", "put", "create_file", "delete", "list_paths", "update_json"):
        real = getattr(env.store, name)
        monkeypatch.setattr(env.store, name,
                            (lambda real, name: lambda *a, **k: (calls.append(name),
                                                                 real(*a, **k))[1])(real, name))
    clock = [OPENS + timedelta(minutes=30)]

    def sleep(s):
        clock[0] += timedelta(seconds=s)
        env.store.now = clock[0]

    burst_loop(env.store, env.tg, env.claims, lambda: clock[0], sleep,
               OPENS + timedelta(hours=4), fetch_fn=env.web.fetch, max_seconds=3600,
               rand=lambda a, b: a)  # always the shortest tick
    # FakeStore.update_json goes through the patched get/put: count those, not the wrapper
    requests = len(calls) - calls.count("update_json")
    assert requests < 600


def test_burst_ends_after_4_h_overdue(monkeypatch):
    env = _overdue_env(monkeypatch, OPENS)
    clock = [OPENS + timedelta(minutes=239)]

    def sleep(s):
        clock[0] += timedelta(seconds=s)

    burst_loop(env.store, env.tg, env.claims, lambda: clock[0], sleep,
               OPENS + timedelta(hours=4), fetch_fn=env.web.fetch)
    assert clock[0] <= OPENS + timedelta(hours=4, seconds=65)
    assert plan_mode(env.saved_state(), config_from_dict(WATCH_CFG),
                     OPENS + timedelta(hours=4, minutes=1)) == ("normal", None)



# ==== fix round 2 ==================================================================

def test_garbage_session_record_does_not_crash_run(monkeypatch):
    env = Env(monkeypatch, [mkplay()], WATCH_CFG)
    env.run(NOW)
    st = env.saved_state()
    st["plays"]["p"]["sessions"]["junk"] = {}
    env.store.files["state.json"] = (st, "x1")
    res = env.run(NOW + timedelta(minutes=5))
    assert res.scanned == 1 and "junk" not in env.saved_state()["plays"]["p"]["sessions"]


def test_old_schema_on_sale_sessions_do_not_start_a_burst(monkeypatch):
    env = Env(monkeypatch, [mkplay(sessions=[sess(status=Status.ON_SALE)])], WATCH_CFG)
    env.run(NOW)
    st = env.saved_state()
    del st["plays"]["p"]["sessions"]["s1"]["status_since"]  # pre-deploy record
    env.store.files["state.json"] = (st, "x1")
    t = NOW + timedelta(hours=3)
    res = env.run(t)
    assert res.state["plays"]["p"]["sessions"]["s1"]["status_since"] is None
    assert plan_mode(res.state, config_from_dict(WATCH_CFG), t) == ("normal", None)


def test_pc_warnings_inside_burst_window(monkeypatch):
    opens = NOW + timedelta(hours=1)
    store = RunsStore()
    env = Env(monkeypatch, [mkplay(sessions=[sess(opens_at=opens)])], WATCH_CFG,
              store=store)
    env.run(NOW)
    env.tg.sent.clear()
    cfg = config_from_dict(WATCH_CFG)
    # a normal run 55 min before opening: first warning
    t = opens - timedelta(minutes=55)
    store.now = t
    st = env.saved_state()
    pc_watch(store, st, cfg, t, env.claims, env.tg)
    runner.save_state(store, st)
    assert [m for m in env.tg.sent if "PC uygulaması aktif değil" in m] != []
    clock = [opens - timedelta(minutes=10)]

    def sleep(s):
        clock[0] += timedelta(seconds=s)
        store.now = clock[0]

    store.now = clock[0]
    res = burst_loop(store, env.tg, env.claims, lambda: clock[0], sleep,
                     opens + timedelta(minutes=90), fetch_fn=env.web.fetch,
                     rand=lambda a, b: 0.0)
    keys = sorted(k for k in res.state["emitted"] if k.startswith("PC_INACTIVE"))
    assert keys == [f"PC_INACTIVE:{opens.isoformat()}:1", f"PC_INACTIVE:{opens.isoformat()}:2"]
    pc_msgs = [m for m in env.tg.sent if "PC uygulaması aktif değil" in m]
    assert len(pc_msgs) == 2
    assert not any(k.startswith("GH_LAGGING") for k in res.state["emitted"])


def test_pc_watch_runs_about_every_60_s_in_burst(monkeypatch):
    opens = NOW + timedelta(minutes=5)
    store = RunsStore()
    env = Env(monkeypatch, [mkplay(sessions=[sess(opens_at=opens)])], WATCH_CFG,
              store=store)
    env.run(NOW)
    calls = []
    real = runner.pc_watch
    monkeypatch.setattr(runner, "pc_watch",
                        lambda *a, **k: (calls.append(a[3]), real(*a, **k))[1])
    clock = [NOW]

    def sleep(s):
        clock[0] += timedelta(seconds=s)
        store.now = clock[0]

    burst_loop(store, env.tg, env.claims, lambda: clock[0], sleep,
               opens + timedelta(minutes=90), fetch_fn=env.web.fetch,
               rand=lambda a, b: 0.0, max_seconds=300)
    assert 4 <= len(calls) <= 6
    assert all(b - a >= timedelta(seconds=60) for a, b in zip(calls, calls[1:]))


def test_burst_save_failure_reaches_exit_code_and_final_state_saved(monkeypatch, capsys):
    _main_env(monkeypatch)
    clock = [datetime(2026, 11, 16, 9, 52, tzinfo=ISTANBUL)]
    opens = clock[0] + timedelta(minutes=5)

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    failed = []

    class S(RunsStore):
        def __init__(self, *a, **k):
            super().__init__()
            self.now = clock[0]

        def ensure_branch(self):
            pass

        def update_json(self, path, mutate, retries=4):
            if path == "state.json" and not failed:
                new = mutate({})
                if new["plays"]["p"]["sessions"]["s1"]["status"] == "on_sale":
                    failed.append(1)
                    raise RuntimeError("HTTP 502")
            return super().update_json(path, mutate, retries)

    made = []
    web = Web(monkeypatch, [mkplay(sessions=[sess(opens_at=opens)])])

    def make_store(*a, **k):
        s = S()
        s.files["config.json"] = (WATCH_CFG, "c")
        made.append(s)
        return s

    def sleep(s):
        clock[0] += timedelta(seconds=s)
        made[0].now = clock[0]
        if clock[0] >= opens:
            web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))

    tgs = []
    monkeypatch.setattr(runner, "datetime", FakeDT)
    monkeypatch.setattr(runner, "Store", make_store)
    monkeypatch.setattr(runner, "Telegram", lambda *a, **k: tgs.append(FakeTG()) or tgs[-1])
    monkeypatch.setattr(runner, "fetch", web.fetch)
    monkeypatch.setattr(runner.time, "sleep", sleep)
    assert runner.main() == 1
    assert failed == [1]
    saved = made[0].files["state.json"][0]
    assert saved["plays"]["p"]["sessions"]["s1"]["status"] == "on_sale"
    assert sum("Bilet alınabilir" in m for m in tgs[0].sent) == 1
    assert "state not saved" in capsys.readouterr().err


def test_burst_dirty_state_is_reported(monkeypatch):
    opens = NOW + timedelta(minutes=5)
    store = FlakyStateStore()
    env = Env(monkeypatch, [mkplay(sessions=[sess(opens_at=opens)])], WATCH_CFG,
              store=store)
    env.run(NOW)
    clock = [NOW]

    def sleep(s):
        clock[0] += timedelta(seconds=s)
        if clock[0] >= opens:
            env.web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))
            store.fail_state = True

    res = burst_loop(store, env.tg, env.claims, lambda: clock[0], sleep,
                     opens + timedelta(minutes=10), fetch_fn=env.web.fetch,
                     rand=lambda a, b: 0.0)
    assert res.save_error == "RuntimeError" and res.dirty is True
    assert env.saved_state()["plays"]["p"]["sessions"]["s1"]["status"] == "upcoming"



# ==== fix round 3: GitHub cadence checked once, at run start =======================

class CadenceStore(FakeStore):
    """Realistic run list: the current in-progress run (id 999 = GITHUB_RUN_ID) on top,
    then completed scan runs with fixed start/end times."""

    def __init__(self, now, previous_ends):
        super().__init__(now=now)
        self.run_start = now
        self.previous_ends = previous_ends  # completion times, newest first

    def ensure_branch(self):
        pass

    def runs(self, per_page=10):
        out = [{"id": 999, "path": ".github/workflows/scan.yml", "status": "in_progress",
                "conclusion": None, "created_at": self.run_start.isoformat(),
                "updated_at": self.now.isoformat()}]
        for i, end in enumerate(self.previous_ends):
            out.append({"id": 900 - i, "path": ".github/workflows/scan.yml",
                        "status": "completed", "conclusion": "success",
                        "created_at": (end - timedelta(minutes=1)).isoformat(),
                        "updated_at": end.isoformat()})
        return out


def _main_burst_run(monkeypatch, previous_end_before_start_min):
    """main() starting 10 min before an opening (burst), PC never active."""
    _main_env(monkeypatch)
    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    start = datetime(2026, 11, 16, 9, 50, tzinfo=ISTANBUL)
    opens = start + timedelta(minutes=10)
    clock = [start]

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    prev_end = start - timedelta(minutes=previous_end_before_start_min)
    store = CadenceStore(start, [prev_end, prev_end - timedelta(minutes=5)])
    store.files["config.json"] = (WATCH_CFG, "c")
    web = Web(monkeypatch, [mkplay(sessions=[sess(opens_at=opens)])])
    # earlier runs already saw the play (not a first scan)
    seed = FakeStore(now=start)
    run_once(seed, FakeTG(), ClaimStore(seed, "github", lambda: start),
             start - timedelta(hours=1), fetch_fn=web.fetch, sleep=lambda s: None)
    store.files["state.json"] = seed.files["state.json"]

    def sleep(s):
        clock[0] += timedelta(seconds=s)
        store.now = clock[0]
        if clock[0] >= opens + timedelta(minutes=2):
            web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))

    tgs = []
    monkeypatch.setattr(runner, "datetime", FakeDT)
    monkeypatch.setattr(runner, "Store", lambda *a, **k: store)
    monkeypatch.setattr(runner, "Telegram", lambda *a, **k: tgs.append(FakeTG()) or tgs[-1])
    monkeypatch.setattr(runner, "fetch", web.fetch)
    monkeypatch.setattr(runner.time, "sleep", sleep)
    code = runner.main()
    return code, store, tgs[0], clock[0] - start


def test_healthy_cadence_burst_has_no_gh_lagging(monkeypatch):
    # previous scan ended 14 min before the opening = 4 min before this run started
    code, store, tg, elapsed = _main_burst_run(monkeypatch, 4)
    assert elapsed >= timedelta(minutes=10)  # the burst ran past the opening
    emitted = store.files["state.json"][0]["emitted"]
    assert not any(k.startswith("GH_LAGGING") for k in emitted)
    assert not any("GitHub taraması gecikiyor" in m for m in tg.sent)
    assert sum("Bilet alınabilir" in m for m in tg.sent) == 1
    assert sum("PC uygulaması aktif değil" in m for m in tg.sent) == 1  # :2 only
    assert code == 0


def test_late_cadence_gh_lagging_once(monkeypatch):
    # previous scan ended 40 min before this run started: genuinely late
    code, store, tg, _ = _main_burst_run(monkeypatch, 40)
    emitted = store.files["state.json"][0]["emitted"]
    assert len([k for k in emitted if k.startswith("GH_LAGGING")]) == 1
    assert sum("GitHub taraması gecikiyor" in m for m in tg.sent) == 1


def test_pc_watch_check_gh_false_skips_github_check():
    opens = NOW + timedelta(minutes=30)
    store = RunsStore(runs_age_min=40)  # would be unhealthy
    st, cfg, tg, claims = _pc_env(store, opens)
    ev = pc_watch(store, st, cfg, NOW, claims, tg, check_gh=False)
    assert [e.kind for e in ev] == ["PC_INACTIVE"]  # PC checks still run
    ev = pc_watch(store, st, cfg, NOW, claims, tg)
    assert [e.kind for e in ev] == ["GH_LAGGING"]



# ==== 6c: 4-hour overdue window, request rate, per-tick cost ======================

def test_three_hours_overdue_polls_every_15_s(monkeypatch):
    env = _overdue_env(monkeypatch, OPENS)
    start = OPENS + timedelta(hours=3)
    assert plan_mode(env.saved_state(), config_from_dict(WATCH_CFG), start) == (
        "burst", OPENS + timedelta(hours=4))
    env.web.calls.clear()
    clock = [start]
    ticks, fetch_times = [], []

    def fetch_fn(url):
        fetch_times.append(clock[0])
        return env.web.fetch(url)

    def sleep(s):
        if s >= 5:
            ticks.append(s)
        clock[0] += timedelta(seconds=s)

    burst_loop(env.store, env.tg, env.claims, lambda: clock[0], sleep,
               OPENS + timedelta(hours=4), fetch_fn=fetch_fn, rand=lambda a, b: 0.0)
    assert ticks and set(ticks) == {15}
    # request rate per relevant play: at most 5 per minute (every 12-18 s)
    for t in fetch_times:
        window = [u for u in fetch_times if t <= u < t + timedelta(minutes=1)]
        assert len(window) <= 5
    assert set(env.web.calls) == {BASE + "p"}


def test_fast_tier_tick_cost_is_low(monkeypatch):
    opens = NOW + timedelta(minutes=5)
    env = Env(monkeypatch, [mkplay(sessions=[sess(opens_at=opens)]), mkplay("other")],
              WATCH_CFG)
    env.run(NOW)
    env.web.calls.clear()
    real_get = env.store.get
    gets = []

    def get(path):
        gets.append(path)
        return real_get(path)

    monkeypatch.setattr(env.store, "get", get)
    clock = [NOW]
    ticks = []

    def sleep(s):
        if s >= 5:
            ticks.append(s)
        clock[0] += timedelta(seconds=s)

    burst_loop(env.store, env.tg, env.claims, lambda: clock[0], sleep,
               opens + timedelta(minutes=10), fetch_fn=env.web.fetch,
               rand=lambda a, b: 0.0, max_seconds=300)
    assert set(ticks) == {15}
    n_ticks = len(ticks)
    assert env.web.calls == [BASE + "p"] * n_ticks  # one page per tick, nothing else
    # config is read exactly once per tick (+1 for a final tick that ends the loop)
    assert n_ticks <= gets.count("config.json") <= n_ticks + 1



# ==== 6c fix: no stale config inside a burst ======================================

PAUSED_CFG = {**WATCH_CFG, "paused": True}


def test_unpause_during_burst_flip_is_sent(monkeypatch):
    opens = NOW + timedelta(minutes=5)
    env = Env(monkeypatch, [mkplay(sessions=[sess(opens_at=opens)])], WATCH_CFG)
    env.run(NOW)
    env.tg.sent.clear()
    env.store.files["config.json"] = (PAUSED_CFG, "paused")
    start = opens - timedelta(seconds=3)
    clock = [start]

    def sleep(s):
        clock[0] += timedelta(seconds=s)
        if clock[0] >= start + timedelta(seconds=1):
            env.store.files["config.json"] = (WATCH_CFG, "unpaused")
        if clock[0] >= start + timedelta(seconds=5):
            env.web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))

    burst_loop(env.store, env.tg, env.claims, lambda: clock[0], sleep,
               opens + timedelta(minutes=10), fetch_fn=env.web.fetch,
               rand=lambda a, b: 0.0)
    assert sum("Bilet alınabilir" in m for m in env.tg.sent) == 1


def test_stale_paused_config_never_discards(monkeypatch):
    env = on_sale_ready(monkeypatch)  # stored config: not paused
    stale = config_from_dict(PAUSED_CFG)
    res = env.run(NOW + timedelta(minutes=5), config=stale)
    assert [e.kind for e in res.events] == ["ON_SALE"]
    assert len(env.tg.sent) == 1 and "Bilet alınabilir" in env.tg.sent[0]
    assert env.saved_state()["meta"].get("paused_discarded", 0) == 0


def test_really_paused_still_discards(monkeypatch):
    env = on_sale_ready(monkeypatch)
    env.store.files["config.json"] = (PAUSED_CFG, "p2")
    res = env.run(NOW + timedelta(minutes=5))
    assert [e.kind for e in res.events] == ["ON_SALE"] and env.tg.sent == []
    assert env.saved_state()["meta"]["paused_discarded"] == 1


def test_watch_added_mid_burst_honored_next_tick(monkeypatch):
    opens = NOW + timedelta(minutes=5)
    p = mkplay("p", sessions=[sess(opens_at=opens)])
    q = mkplay("q", sessions=[sess("s2", opens_at=opens)])
    env = Env(monkeypatch, [p, q], WATCH_CFG)
    env.run(NOW)
    env.tg.sent.clear()
    start = opens - timedelta(seconds=3)
    clock = [start]
    sent_at = []
    real_send = env.tg.send

    def send(text):
        sent_at.append((clock[0], text))
        return real_send(text)

    env.tg.send = send
    with_q = {"plays": {"p": WATCH_CFG["plays"]["p"], "q": WATCH_CFG["plays"]["p"]}}

    def sleep(s):
        clock[0] += timedelta(seconds=s)
        if clock[0] >= start + timedelta(seconds=1):
            env.store.files["config.json"] = (with_q, "q-added")
        if clock[0] >= start + timedelta(seconds=5):
            env.web.set(mkplay("q", sessions=[sess("s2", status=Status.ON_SALE)]))

    burst_loop(env.store, env.tg, env.claims, lambda: clock[0], sleep,
               opens + timedelta(minutes=10), fetch_fn=env.web.fetch,
               rand=lambda a, b: 0.0, max_seconds=120)
    on_sale = [t for t, m in sent_at if "Bilet alınabilir" in m]
    assert len(on_sale) == 1 and on_sale[0] <= start + timedelta(seconds=16)


# ==== final review I1: burst after a sell-out =======================================

@pytest.mark.parametrize("before", ["upcoming", "on_sale"])
def test_plan_mode_bursts_for_30_min_after_a_sell_out(before):
    st = _state_with_opening(OPENS, status="sold_out")
    rec = st["plays"]["p"]["sessions"]["s1"]
    sold = OPENS + timedelta(minutes=2)
    rec.update(status_since=sold.isoformat(), prev_status=before)
    cfg = config_from_dict(WATCH_CFG)
    assert plan_mode(st, cfg, sold + timedelta(minutes=29)) == (
        "burst", sold + timedelta(minutes=30))
    assert plan_mode(st, cfg, sold + timedelta(minutes=31)) == ("normal", None)


def test_first_seen_sold_out_does_not_burst():
    st = _state_with_opening(OPENS, status="sold_out")
    rec = st["plays"]["p"]["sessions"]["s1"]
    rec.update(status_since=NOW.isoformat(), prev_status=None)
    assert plan_mode(st, config_from_dict(WATCH_CFG), NOW + timedelta(minutes=1)) == (
        "normal", None)


# ==== final review C1: ON_SALE dedupe per session and time, not per counter =========

class _PC:
    """Second runner (who="pc") on the same store with its own in-memory state."""

    def __init__(self, env, state):
        self.env, self.state = env, state
        self.tg = FakeTG()
        self.claims = ClaimStore(env.store, "pc", lambda: env.store.now)

    def run(self, now):
        self.env.store.now = now
        res = run_once(self.env.store, self.tg, self.claims, now, who="pc",
                       persist_state=False, state=self.state, fetch_fn=self.env.web.fetch,
                       sleep=lambda s: None)
        self.state = res.state
        return res


def _two_runners(monkeypatch):
    env = Env(monkeypatch, [mkplay()], WATCH_CFG)
    env.claims = ClaimStore(env.store, "github", lambda: env.store.now)
    env.run(NOW)
    env.tg.sent.clear()
    return env, _PC(env, copy.deepcopy(env.saved_state()))


def _status(env, status):
    env.web.set(mkplay(sessions=[sess(status=status)]))


def test_counter_drift_never_suppresses_a_later_reopen(monkeypatch):
    env, pc = _two_runners(monkeypatch)
    day = NOW.replace(hour=10)
    # 10:00 flip: GitHub sends #1; the PC sees the same flip and stays silent
    _status(env, Status.ON_SALE)
    env.run(day)
    pc.run(day + timedelta(seconds=5))
    pc.run(day + timedelta(seconds=25))
    assert len(env.tg.sent) == 1 and pc.tg.sent == []
    _status(env, Status.SOLD_OUT)
    env.run(day + timedelta(minutes=1))
    pc.run(day + timedelta(minutes=1, seconds=5))
    # 12:53 iade only the PC catches -> #2 (PC counter 2, GitHub counter stays 1)
    _status(env, Status.ON_SALE)
    t = day.replace(hour=12, minute=53)
    pc.run(t)
    pc.run(t + timedelta(seconds=70))
    assert len(pc.tg.sent) == 1
    _status(env, Status.SOLD_OUT)
    pc.run(t + timedelta(minutes=3))
    env.run(t + timedelta(minutes=4))
    # 15:00 iade GitHub sees: its counter derives ON_SALE:s1:2 -> must still be sent (#3)
    _status(env, Status.ON_SALE)
    res = env.run(day.replace(hour=15))
    assert [e.key for e in res.events] == ["ON_SALE:s1:2"]
    assert len(env.tg.sent) == 2
    assert sum("Bilet alınabilir" in m for m in env.tg.sent + pc.tg.sent) == 3


def test_same_flip_seen_by_both_runners_sends_exactly_one(monkeypatch):
    env, pc = _two_runners(monkeypatch)
    day = NOW.replace(hour=10)
    _status(env, Status.ON_SALE)
    pc.run(day)  # the PC sees it first and queues it with its grace delay
    env.run(day + timedelta(seconds=10))  # GitHub sees the same flip inside the grace
    for s in (20, 40, 70, 130, 400):
        pc.run(day + timedelta(seconds=s))
    env.run(day + timedelta(minutes=10))
    assert len(env.tg.sent) + len(pc.tg.sent) == 1
    assert pc.state["outbox"] == []


def test_outbox_skip_check_honours_the_reclaim_window(monkeypatch):
    """A sent ON_SALE claim covers events created within the 3-min gap after it, even if
    the entry is delivered later (e.g. a PC that was busy)."""
    env, pc = _two_runners(monkeypatch)
    day = NOW.replace(hour=10)
    _status(env, Status.ON_SALE)
    env.run(day)
    pc.run(day + timedelta(seconds=30))  # queued, not_before + grace
    pc.run(day + timedelta(minutes=8))  # delivered late: still the same episode
    assert len(env.tg.sent) == 1 and pc.tg.sent == [] and pc.state["outbox"] == []


# ==== final review I3: PC grace 15 s for ON_SALE, 60 s for everything else ==========

def test_pc_grace_is_15_s_for_on_sale_and_60_s_for_other_kinds(monkeypatch):
    env, pc = _two_runners(monkeypatch)
    t = NOW + timedelta(minutes=5)
    _status(env, Status.ON_SALE)
    env.web.venue.append("q")
    env.web.set(mkplay("q", sessions=[]))
    res = pc.run(t)
    box = {e["key"]: e["not_before"] for e in res.state["outbox"]}
    assert box == {"ON_SALE:s1:1": (t + timedelta(seconds=15)).isoformat(),
                   "NEW_PLAY:q": (t + timedelta(seconds=60)).isoformat()}
    pc.run(t + timedelta(seconds=16))
    assert len(pc.tg.sent) == 1 and "Bilet alınabilir" in pc.tg.sent[0]


# ==== final review I6: an UNKNOWN session inside its opening window keeps bursting ==

def test_unknown_session_in_opening_window_stays_a_burst_target(monkeypatch):
    opens = NOW + timedelta(minutes=5)
    env = Env(monkeypatch, [mkplay(sessions=[sess(opens_at=opens)])], WATCH_CFG)
    env.run(NOW)
    odd = Session("s1", SAT_20, None, Status.UNKNOWN, None, f"{BASE}x/s1", "Sahne")
    env.web.set(mkplay(sessions=[odd]))
    res = env.run(opens, only_slugs=["p"])
    rec = res.state["plays"]["p"]["sessions"]["s1"]
    assert rec["status"] == "unknown" and rec["opens_at"] == opens.isoformat()
    assert plan_mode(res.state, config_from_dict(WATCH_CFG), opens + timedelta(minutes=30)) \
        == ("burst", opens + timedelta(hours=4))


# ==== final review I5: burst-mode fetch failures are reported once ==================

def test_burst_all_fetches_failing_emits_one_fetch_failing(monkeypatch):
    opens = NOW + timedelta(minutes=5)
    env = Env(monkeypatch, [mkplay(sessions=[sess(opens_at=opens)])], WATCH_CFG)
    env.run(NOW)
    env.tg.sent.clear()
    env.web.plays["p"] = FetchError("HTTP 503: blocked")
    clock = [opens]
    ticks = []

    def sleep(s):
        if s >= 5:
            ticks.append(s)
        clock[0] += timedelta(seconds=s)
        env.store.now = clock[0]

    res = burst_loop(env.store, env.tg, env.claims, lambda: clock[0], sleep,
                     opens + timedelta(hours=4), fetch_fn=env.web.fetch,
                     rand=lambda a, b: 0.0, max_seconds=60 * 15)
    assert len(ticks) == 60
    failing = [m for m in env.tg.sent if "taraması başarısız" in m]
    assert len(failing) == 1 and "hızlı" in failing[0].lower()
    keys = [k for k in res.state["emitted"] if k.startswith("FETCH_FAILING:burst:")]
    assert keys == [f"FETCH_FAILING:burst:{opens.isoformat()}"]


def test_burst_fetch_failure_streak_resets_on_success(monkeypatch):
    opens = NOW + timedelta(minutes=5)
    good = mkplay(sessions=[sess(opens_at=opens)])
    env = Env(monkeypatch, [good], WATCH_CFG)
    env.run(NOW)
    env.tg.sent.clear()
    clock = [opens]
    n = [0]

    def sleep(s):
        clock[0] += timedelta(seconds=s)
        n[0] += 1
        # 4 failing ticks, 1 good tick, repeated: never 5 in a row
        env.web.plays["p"] = good if n[0] % 4 == 0 else FetchError("HTTP 503: x")

    env.web.plays["p"] = FetchError("HTTP 503: x")
    burst_loop(env.store, env.tg, env.claims, lambda: clock[0], sleep,
               opens + timedelta(hours=4), fetch_fn=env.web.fetch,
               rand=lambda a, b: 0.0, max_seconds=600)
    assert not any("taraması başarısız" in m for m in env.tg.sent)


# ==== final review I4: a run that starts inside a burst window bursts first =========

def test_main_starting_in_burst_window_bursts_before_the_normal_scan(monkeypatch):
    _main_env(monkeypatch)
    start = datetime(2026, 11, 16, 9, 55, tzinfo=ISTANBUL)
    opens = start + timedelta(minutes=2)
    clock = [start]

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    store = CadenceStore(start, [start - timedelta(minutes=4)])
    store.files["config.json"] = (WATCH_CFG, "c")
    web = Web(monkeypatch, [mkplay(sessions=[sess(opens_at=opens)])])
    seed = FakeStore(now=start)
    run_once(seed, FakeTG(), ClaimStore(seed, "github", lambda: start),
             start - timedelta(hours=1), fetch_fn=web.fetch, sleep=lambda s: None)
    store.files["state.json"] = seed.files["state.json"]
    web.calls.clear()
    fetched = []

    def fetch(url):
        fetched.append((clock[0], url))
        return web.fetch(url)

    def sleep(s):
        clock[0] += timedelta(seconds=s)
        store.now = clock[0]
        if clock[0] >= opens:
            web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))

    tgs = []
    monkeypatch.setattr(runner, "datetime", FakeDT)
    monkeypatch.setattr(runner, "Store", lambda *a, **k: store)
    monkeypatch.setattr(runner, "Telegram", lambda *a, **k: tgs.append(FakeTG()) or tgs[-1])
    monkeypatch.setattr(runner, "fetch", fetch)
    monkeypatch.setattr(runner.time, "sleep", sleep)
    assert runner.main() == 0
    assert fetched[0] == (start, BASE + "p")  # no full venue scan before the burst
    venue = [t for t, u in fetched if u == DEFAULT_SOURCE]
    assert venue and venue[0] > opens  # the normal scan runs after the burst
    assert sum("Bilet alınabilir" in m for m in tgs[0].sent) == 1


# ==== final review M1: main() survives a transient config GET and an invalid config ==

def _main_normal(monkeypatch, store, web):
    _main_env(monkeypatch)
    tgs = []
    monkeypatch.setattr(runner, "Store", lambda *a, **k: store)
    monkeypatch.setattr(runner, "Telegram", lambda *a, **k: tgs.append(FakeTG()) or tgs[-1])
    monkeypatch.setattr(runner, "fetch", web.fetch)
    clock = [NOW]

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    def sleep(s):  # simulated time, so a burst tail ends quickly
        clock[0] += timedelta(seconds=s)
        store.now = clock[0]

    store.now = NOW
    monkeypatch.setattr(runner, "datetime", FakeDT)
    monkeypatch.setattr(runner.time, "sleep", sleep)
    return tgs


class FlakyGetStore(RunsStore):
    def __init__(self, fail_paths):
        super().__init__()
        self.fail_paths = dict(fail_paths)  # path -> remaining failures

    def ensure_branch(self):
        pass

    def get(self, path):
        if self.fail_paths.get(path, 0) > 0:
            self.fail_paths[path] -= 1
            raise RuntimeError("HTTP 502")
        return super().get(path)


def test_main_retries_a_transient_config_and_state_get_once(monkeypatch):
    web = Web(monkeypatch, [mkplay()])
    store = FlakyGetStore({"config.json": 1, "state.json": 1})
    store.files["config.json"] = (WATCH_CFG, "c")
    tgs = _main_normal(monkeypatch, store, web)
    assert runner.main() == 0
    assert "p" in store.files["state.json"][0]["plays"]
    assert len(tgs[0].sent) == 1  # the setup message: the run was not lost


def test_main_invalid_config_alerts_once_and_scans_with_the_cached_config(monkeypatch):
    web = Web(monkeypatch, [mkplay()])
    store = FlakyGetStore({})
    store.files["config.json"] = (WATCH_CFG, "c")
    tgs = _main_normal(monkeypatch, store, web)
    assert runner.main() == 0
    assert store.files["state.json"][0]["meta"]["config_cache"] == WATCH_CFG
    bad = {"plays": {"p": {"active": True, "targets": [
        {"id": "t1", "name": "Aksam", "people": 9, "rule": {}}]}}}
    store.files["config.json"] = (bad, "bad")
    web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))
    assert runner.main() == 0
    sent = tgs[1].sent
    assert sum("config.json" in m and "geçersiz" in m for m in sent) == 1
    assert sum("Bilet alınabilir" in m for m in sent) == 1  # cached watch still alerts
    assert runner.main() == 0
    assert not any("geçersiz" in m for m in tgs[2].sent)  # alerted once
    assert store.files["state.json"][0]["meta"]["config_cache"] == WATCH_CFG
