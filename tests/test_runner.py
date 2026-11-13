from __future__ import annotations

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
    claim = env.store.files[claim_path("ON_SALE:s1:1")][0]
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
                                              (9, "burst"), (11, "normal")])
def test_plan_mode_window(offset_min, mode):
    st = _state_with_opening(OPENS)
    cfg = config_from_dict(WATCH_CFG)
    got, until = plan_mode(st, cfg, OPENS + timedelta(minutes=offset_min))
    assert got == mode
    assert until == (OPENS + timedelta(minutes=10) if mode == "burst" else None)


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
    assert clock[0] <= NOW + timedelta(seconds=780 + 12)
    assert len(env.tg.sent) == 1 and "Bilet alınabilir" in env.tg.sent[0]
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
    assert clock[0] - NOW <= timedelta(seconds=72)
    clock[0] = NOW
    burst_loop(env.store, env.tg, env.claims, lambda: clock[0], sleep,
               NOW + timedelta(seconds=30), fetch_fn=env.web.fetch)
    assert clock[0] - NOW <= timedelta(seconds=36)


# ---- (e) pc_watch ------------------------------------------------------------

class RunsStore(FakeStore):
    def __init__(self, runs_age_min=2):
        super().__init__()
        self.runs_age_min = runs_age_min

    def runs(self, per_page=10):
        t = (self.now - timedelta(minutes=self.runs_age_min)).isoformat()
        return [{"created_at": t, "status": "completed", "conclusion": "success"}]


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
    assert st["plays"]["b"] == before
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
    path = claim_path("ON_SALE:s1:1")
    rec, sha = env.store.get(path)
    env.store.put(path, {**rec, "status": "sent", "claimed_by": "pc"}, sha)
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
    assert st["outbox"] == [] and res.failures >= 1
    assert st["meta"]["outbox_dropped"][-1]["key"] == "ON_SALE:s1:1"


def test_outbox_drops_after_20_attempts(monkeypatch):
    env = on_sale_ready(monkeypatch)
    env.tg.fail = True
    seen = []
    for i in range(25):  # every 10 min: the retry back-off (max 10 min) has elapsed
        t = NOW + timedelta(minutes=5 + 10 * i)
        env.claims.clock = lambda t=t: t
        env.run(t)
        seen.append([e["attempts"] for e in env.saved_state()["outbox"]])
    # the 20th failed attempt drops the entry in the same run
    assert seen[0] == [1] and seen[18] == [19] and seen[19] == [], seen
    st = env.saved_state()
    assert st["outbox"] == []
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
    claim = env.store.files[claim_path("ON_SALE:big:1")][0]
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
    key = f"SETUP:{NOW:%Y-%m-%dT%H:%M}"
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
    assert res.scanned == 3 and res.failures == 3
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
    assert box[0]["not_before"] == (t + timedelta(seconds=60)).isoformat()

    t2 = t + timedelta(seconds=61)
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
    res = env.run(NOW + timedelta(minutes=10))
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
