from __future__ import annotations

import copy
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from pc.service import RefreshError, Service, TickResult
from tests.conftest import FakeStore
from tests.test_runner import NOW, OPENS, SAT_20, WATCH_CFG, FakeTG, Web, mkplay, sess
from tiyatro.claims import ClaimStore, claim_path
from tiyatro.config import DEFAULT_SOURCE
from tiyatro.fetcher import FetchError
from tiyatro.models import Status
from tiyatro.runner import run_once


# ---- fakes ---------------------------------------------------------------------

class Clock:
    def __init__(self, store, t):
        self.store = store
        self.set(t)

    def __call__(self):
        return self.t

    def set(self, t):
        self.t = t
        self.store.now = t  # claims use the server clock


class RunsStore(FakeStore):
    def __init__(self, runs=None):
        super().__init__()
        self.runs_list = runs or []
        self.runs_calls = 0

    def runs(self, per_page=10):
        self.runs_calls += 1
        return copy.deepcopy(self.runs_list)


def make(monkeypatch, tmp_path, plays=None, config=WATCH_CFG, at=NOW, runs=None,
         github_scan=True):
    store = RunsStore(runs)
    store.files["config.json"] = (copy.deepcopy(config), "cfg")
    web = Web(monkeypatch, plays if plays is not None else [mkplay()])
    clock = Clock(store, at)
    gh_tg = FakeTG()
    gh_claims = ClaimStore(store, "github", clock)
    if github_scan:
        run_once(store, gh_tg, gh_claims, at, fetch_fn=web.fetch, sleep=lambda s: None)
    tg = FakeTG()
    svc = Service(store, tg, ClaimStore(store, "pc", clock), clock=clock,
                  fetch_fn=web.fetch, sleep=lambda s: None, local_dir=tmp_path,
                  rand=lambda a, b: 0.0)
    notes = []
    svc.on_notify(lambda title, text, events: notes.append((title, text, events)))

    def gh_run(t):
        clock.set(t)
        return run_once(store, gh_tg, gh_claims, t, fetch_fn=web.fetch, sleep=lambda s: None)

    return SimpleNamespace(store=store, web=web, clock=clock, tg=tg, gh_tg=gh_tg, svc=svc,
                           notes=notes, gh_run=gh_run, tmp=tmp_path)


def tick_at(e, t) -> TickResult:
    e.clock.set(t)
    return e.svc.tick()


def note_keys(e):
    return [ev.key for _t, _x, evs in e.notes for ev in evs]


# ---- baseline, local persistence, cadence -----------------------------------------

def test_first_tick_uses_github_state_and_persists_locally(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    gh_state = e.store.files["state.json"]
    r = tick_at(e, NOW + timedelta(minutes=1))
    assert r.scanned is True and r.events == [] and r.errors == []
    assert e.tg.sent == [] and e.notes == []  # no NEW_PLAY storm: GitHub state is the base
    assert e.store.files["state.json"] is gh_state  # the PC never writes state.json
    local = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "p" in local["plays"]
    assert not list(tmp_path.glob("*.tmp"))  # atomic write leaves no temp file


def test_local_state_is_reused_after_restart(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    e.store.files["state.json"] = ({"plays": {}}, "other")  # GitHub side changed
    svc2 = Service(e.store, FakeTG(), ClaimStore(e.store, "pc", e.clock), clock=e.clock,
                   fetch_fn=e.web.fetch, sleep=lambda s: None, local_dir=tmp_path)
    assert "p" in svc2.state["plays"]


def test_normal_cadence_scans_every_300_s(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    assert tick_at(e, NOW).scanned is True
    assert e.svc.scan_interval(NOW) == 300
    assert e.svc.next_interval(NOW) == 300
    calls = len(e.web.calls)
    assert tick_at(e, NOW + timedelta(seconds=60)).scanned is False
    assert len(e.web.calls) == calls  # nothing fetched
    assert e.svc.next_interval(NOW + timedelta(seconds=60)) == 240
    assert tick_at(e, NOW + timedelta(seconds=300)).scanned is True


def _opening_env(monkeypatch, tmp_path, opens_at, plays_extra=()):
    plays = [mkplay(sessions=[sess(opens_at=opens_at)]), *plays_extra]
    return make(monkeypatch, tmp_path, plays=plays)


@pytest.mark.parametrize("opens_delta_min, interval", [
    (5, 12),       # inside the 10 min lead: 12 s
    (-40, 30),     # 40 min overdue and still upcoming: 30 s
    (-100, 60),    # 100 min overdue: 60 s
    (60 * 24, 300),  # far away: normal
])
def test_scan_interval_follows_burst_tiers(monkeypatch, tmp_path, opens_delta_min,
                                           interval):
    e = _opening_env(monkeypatch, tmp_path, NOW + timedelta(minutes=opens_delta_min))
    r = tick_at(e, NOW)
    assert r.mode == ("normal" if interval == 300 else "burst")
    assert e.svc.scan_interval(NOW) == interval


def test_burst_tick_scans_only_burst_plays(monkeypatch, tmp_path):
    other = mkplay("q")
    e = _opening_env(monkeypatch, tmp_path, NOW + timedelta(minutes=5), [other])
    tick_at(e, NOW)
    e.web.calls.clear()
    r = tick_at(e, NOW + timedelta(seconds=12))
    assert r.scanned is True and r.mode == "burst"
    assert e.web.calls == ["https://biletinial.com/tr-tr/tiyatro/p"]


def test_normal_scan_is_pulled_forward_to_the_burst_start(monkeypatch, tmp_path):
    e = _opening_env(monkeypatch, tmp_path, NOW + timedelta(minutes=12))
    tick_at(e, NOW)
    assert e.svc.next_interval(NOW) == 120  # burst starts at opens - 10 min


# ---- heartbeat ---------------------------------------------------------------------

def test_no_heartbeat_outside_the_window(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)  # opening is days away
    r = tick_at(e, NOW)
    assert "heartbeat.json" not in e.store.files
    assert r.status["heartbeat_age_s"] is None


def test_heartbeat_inside_the_window_with_observed(monkeypatch, tmp_path):
    e = _opening_env(monkeypatch, tmp_path, NOW + timedelta(minutes=30),
                     [mkplay("q")])  # q is not watched: not in observed
    r = tick_at(e, NOW)
    hb = e.store.files["heartbeat.json"][0]
    assert hb["ts"] == NOW.isoformat()
    assert hb["last_scan_ok"] == NOW.isoformat()
    assert hb["observed"] == {"s1": "upcoming"}
    assert r.next_opening == NOW + timedelta(minutes=30)
    # not rewritten before 4 min, rewritten after
    tick_at(e, NOW + timedelta(seconds=60))
    assert e.store.files["heartbeat.json"][0]["ts"] == NOW.isoformat()
    assert e.svc.next_interval(NOW + timedelta(seconds=60)) == 180
    tick_at(e, NOW + timedelta(seconds=240))
    assert e.store.files["heartbeat.json"][0]["ts"] == (NOW + timedelta(seconds=240)).isoformat()


def test_heartbeat_during_burst(monkeypatch, tmp_path):
    e = _opening_env(monkeypatch, tmp_path, NOW - timedelta(minutes=20))  # overdue
    tick_at(e, NOW)
    assert e.store.files["heartbeat.json"][0]["observed"] == {"s1": "upcoming"}


# ---- takeover (GitHub first, PC after 60 s) -------------------------------------------

def _flip_on_sale(e, t):
    e.web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))
    return tick_at(e, t)


def test_pc_never_sends_when_github_already_sent(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    t = NOW + timedelta(minutes=10)
    r = _flip_on_sale(e, t)
    assert [ev.kind for ev in r.events] == ["ON_SALE"]
    assert e.tg.sent == []
    n = len(e.gh_tg.sent)
    e.gh_run(t + timedelta(seconds=20))
    assert len(e.gh_tg.sent) == n + 1
    for s in (70, 130, 400):
        tick_at(e, t + timedelta(seconds=s))
    assert e.tg.sent == []
    assert e.svc.state["outbox"] == []
    # the desktop still saw the alert, exactly once
    assert note_keys(e) == [r.events[0].key]


def test_pc_sends_after_60_s_when_github_is_silent(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    t = NOW + timedelta(minutes=10)
    r = _flip_on_sale(e, t)
    key = r.events[0].key
    assert e.svc.next_interval(t) == 12  # burst tail after the flip
    tick_at(e, t + timedelta(seconds=30))
    assert e.tg.sent == []
    tick_at(e, t + timedelta(seconds=61))
    assert len(e.tg.sent) == 1 and "Bilet alınabilir" in e.tg.sent[0]
    assert e.store.files[claim_path(key)][0]["status"] == "sent"
    tick_at(e, t + timedelta(seconds=120))
    assert len(e.tg.sent) == 1
    assert note_keys(e) == [key]  # desktop: once, de-duplicated
    # GitHub sees the claim and does not duplicate
    n = len(e.gh_tg.sent)
    e.gh_run(t + timedelta(seconds=150))
    assert len(e.gh_tg.sent) == n


def test_outbox_delivery_without_a_scan_in_normal_mode(monkeypatch, tmp_path):
    """An event queued by a normal scan is delivered 60 s later by a delivery-only tick."""
    e = make(monkeypatch, tmp_path, config={"plays": {"p": {"active": False}}})
    tick_at(e, NOW)
    e.web.venue.append("r")
    e.web.set(mkplay("r"))
    t = NOW + timedelta(minutes=5)
    r = tick_at(e, t)
    assert [ev.kind for ev in r.events] == ["NEW_PLAY"]
    assert e.svc.next_interval(t) == 60
    calls = len(e.web.calls)
    r2 = tick_at(e, t + timedelta(seconds=60))
    assert r2.scanned is False and len(e.web.calls) == calls
    assert len(e.tg.sent) == 1 and "Yeni oyun" in e.tg.sent[0]
    assert e.svc.status()["last_scan"] == t.isoformat()  # delivery is not a scan


def test_paused_events_are_not_shown_on_the_desktop(monkeypatch, tmp_path):
    cfg = copy.deepcopy(WATCH_CFG)
    cfg["paused"] = True
    e = make(monkeypatch, tmp_path, config=cfg)
    tick_at(e, NOW)
    _flip_on_sale(e, NOW + timedelta(minutes=10))
    tick_at(e, NOW + timedelta(minutes=12))
    assert e.notes == [] and e.tg.sent == []


def test_desktop_dedupe_survives_restart(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    t = NOW + timedelta(minutes=10)
    _flip_on_sale(e, t)
    assert len(e.notes) == 1
    svc2 = Service(e.store, FakeTG(), ClaimStore(e.store, "pc", e.clock), clock=e.clock,
                   fetch_fn=e.web.fetch, sleep=lambda s: None, local_dir=tmp_path)
    again = []
    svc2.on_notify(lambda *a: again.append(a))
    e.clock.set(t + timedelta(seconds=30))
    svc2.tick()  # the ON_SALE entry is still in the restored outbox
    assert again == []


def test_restored_outbox_entry_delivered_after_restart_reaches_the_desktop(
        monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    t = NOW + timedelta(minutes=10)
    key = _flip_on_sale(e, t).events[0].key
    (tmp_path / "notified.json").unlink()  # e.g. the app died before showing it
    tg2 = FakeTG()
    svc2 = Service(e.store, tg2, ClaimStore(e.store, "pc", e.clock), clock=e.clock,
                   fetch_fn=e.web.fetch, sleep=lambda s: None, local_dir=tmp_path)
    seen = []
    svc2.on_notify(lambda title, text, evs: seen.extend(ev.key for ev in evs))
    e.clock.set(t + timedelta(seconds=61))
    svc2.tick()
    assert len(tg2.sent) == 1 and svc2.state["outbox"] == []
    assert seen == [key]


def test_notify_callback_failure_is_reported_not_raised(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)

    def boom(*_a):
        raise RuntimeError("tk gone")

    e.svc.on_notify(boom)
    r = _flip_on_sale(e, NOW + timedelta(minutes=10))
    assert any("bildirim" in err for err in r.errors)
    assert len(e.notes) == 1  # the other callback still ran


# ---- GitHub run status ---------------------------------------------------------------

def test_github_runs_read_every_5_min_display_only(monkeypatch, tmp_path):
    runs = [{"id": 1, "status": "completed", "conclusion": "success", "name": "scan",
             "path": ".github/workflows/scan.yml",
             "created_at": (NOW - timedelta(minutes=3)).isoformat(),
             "updated_at": (NOW - timedelta(minutes=2)).isoformat()}]
    e = make(monkeypatch, tmp_path, runs=runs)
    r = tick_at(e, NOW)
    assert r.gh_status["healthy"] is True and r.gh_status["gap_s"] == 120
    assert e.store.runs_calls == 1
    tick_at(e, NOW + timedelta(seconds=60))
    assert e.store.runs_calls == 1
    r3 = tick_at(e, NOW + timedelta(minutes=20))
    assert e.store.runs_calls == 2 and r3.gh_status["healthy"] is False
    assert e.tg.sent == []  # no Telegram from the PC for GitHub lag


def test_store_failure_is_reported_not_raised(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    e.store.fail = True
    r = tick_at(e, NOW + timedelta(seconds=300))
    assert r.errors and all(isinstance(x, str) for x in r.errors)
    assert e.svc.status()["errors"] == r.errors


def test_reload_config_sees_worker_change(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    cfg, sha = e.store.files["config.json"]
    cfg = copy.deepcopy(cfg)
    cfg["plays"]["p"]["targets"].append({"id": "c", "name": "Çocuk", "people": 3,
                                         "rule": {}})
    e.store.files["config.json"] = (cfg, "worker")
    tick_at(e, NOW + timedelta(seconds=30))
    assert [t.id for t in e.svc.config.plays["p"].targets] == ["t1", "c"]


def test_status_for_the_ui(monkeypatch, tmp_path):
    e = _opening_env(monkeypatch, tmp_path, NOW + timedelta(minutes=30))
    tick_at(e, NOW)
    s = e.svc.status()
    assert s["mode"] == "normal" and s["burst_until"] is None and s["paused"] is False
    assert s["last_scan"] == NOW.isoformat() and s["last_scan_ok"] == NOW.isoformat()
    assert s["next_opening"]["title"] == "Oyun p"
    assert s["next_opening"]["at"] == (NOW + timedelta(minutes=30)).isoformat()
    assert s["next_opening"]["text"] == "13 Kasım Cuma 09:30"
    assert s["outbox_pending"] == 0 and s["heartbeat_age_s"] == 0
    assert s["errors"] == []
    e.clock.set(NOW + timedelta(minutes=25))
    s2 = e.svc.status()
    assert s2["mode"] == "burst" and s2["burst_until"] is not None
    assert s2["heartbeat_age_s"] == 25 * 60


# ---- refresh_now --------------------------------------------------------------------

def test_refresh_now_marks_new_and_removed(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path, plays=[mkplay("p"), mkplay("q")])
    tick_at(e, NOW)
    e.web.venue = ["p", "r"]
    e.web.set(mkplay("r", title="Çocuk Masalı"))
    e.web.calls.clear()
    e.clock.set(NOW + timedelta(minutes=1))
    plays = e.svc.refresh_now()
    assert sorted(p.slug for p in plays) == ["p", "r"]
    assert {p.slug: p.tags for p in plays}["r"] == {"çocuk"}
    assert e.svc.new_slugs == {"r"} and e.svc.removed_slugs == {"q"}
    assert e.web.calls.count(DEFAULT_SOURCE) == 1  # one network pass, replayed
    assert "r" in e.svc.state["plays"]  # folded into the local state (events, alerts)
    rows = {r.slug: r for r in e.svc.rows()}
    assert rows["r"].is_new and rows["q"].is_removed and not rows["p"].is_new
    # survives a restart
    svc2 = Service(e.store, FakeTG(), ClaimStore(e.store, "pc", e.clock), clock=e.clock,
                   fetch_fn=e.web.fetch, sleep=lambda s: None, local_dir=tmp_path)
    assert svc2.new_slugs == {"r"} and svc2.removed_slugs == {"q"}


@pytest.mark.parametrize("breakage", ["venue_error", "empty_venue"])
def test_refresh_now_failure_keeps_previous_list(monkeypatch, tmp_path, breakage):
    e = make(monkeypatch, tmp_path, plays=[mkplay("p"), mkplay("q")])
    tick_at(e, NOW)
    e.web.venue = ["p", "q", "r"]
    e.web.set(mkplay("r"))
    e.clock.set(NOW + timedelta(minutes=1))
    first = e.svc.refresh_now()
    state_before = (tmp_path / "state.json").read_text(encoding="utf-8")
    if breakage == "venue_error":
        e.web.venue_error = FetchError("HTTP 403: blocked")
    else:
        e.web.venue = []
    e.clock.set(NOW + timedelta(minutes=2))
    with pytest.raises(RefreshError, match="önceki liste korunuyor"):
        e.svc.refresh_now()
    assert e.svc.plays == first
    assert e.svc.new_slugs == {"r"} and e.svc.removed_slugs == set()
    assert (tmp_path / "state.json").read_text(encoding="utf-8") == state_before


# ---- config editing ---------------------------------------------------------------------

def js_fixture_config():
    """Python copy of worker/test/_fixtures.js makeConfig()."""
    return {
        "plays": {
            "kusursuz-bir-aile": {
                "active": True,
                "targets": [{"id": "y", "name": "Yetişkin", "people": 2,
                             "rule": {"days": [4, 5], "from_time": "18:00", "to_time": None,
                                      "overrides": {"5": {"from": "14:00", "to": None}},
                                      "exclude_dates": ["2030-03-02"]}}],
                "tag_overrides": {"cocuk": False},
            },
            "kucuk-prens": {"active": False, "targets": [], "tag_overrides": {}},
        },
        "sources": ["https://biletinial.com/tr-tr/mekan/ornek-mekan"],
        "paused": False,
        "version": 1,
    }


class RacyStore(FakeStore):
    """The Worker writes config.json between our read and our write (once)."""

    def __init__(self, worker_change):
        super().__init__()
        self.worker_change = worker_change

    def get(self, path):
        data, sha = super().get(path)
        if path == "config.json" and self.worker_change is not None:
            change, self.worker_change = self.worker_change, None
            cur, cur_sha = self.files[path]
            new = copy.deepcopy(cur)
            change(new)
            self.files[path] = (new, cur_sha + "w")
        return data, sha


def editor(tmp_path, store=None):
    store = store or FakeStore()
    store.files.setdefault("config.json", (js_fixture_config(), "c0"))
    svc = Service(store, FakeTG(), ClaimStore(store, "pc", lambda: NOW), clock=lambda: NOW,
                  local_dir=tmp_path)
    return store, svc


def cfg(store):
    return store.files["config.json"][0]


def tgt(c, slug="kusursuz-bir-aile", tid="y"):
    return next(t for t in c["plays"][slug]["targets"] if t["id"] == tid)


KB = "kusursuz-bir-aile"

EDITS = [
    ("set_active", (KB, False)), ("set_active", ("kucuk-prens", True)),
    ("set_active", ("yeni-oyun", True)),
    ("ensure_target", ("kucuk-prens", "c")), ("ensure_target", (KB, "y")),
    ("ensure_target", ("yeni-oyun", "y")),
    ("set_people", (KB, "y", 3)),
    ("set_days", (KB, "y", [6, 0, 0])), ("set_days", (KB, "y", None)),
    ("set_days", (KB, "y", list(range(7)))),
    ("set_days_group", (KB, "y", "wd")), ("set_days_group", (KB, "y", "we")),
    ("set_days_group", (KB, "y", "all")),
    ("set_day", (KB, "y", 0, True)), ("set_day", (KB, "y", 4, False)),
    ("set_hours", (KB, "y", "18:30", "22:00")), ("set_hours", (KB, "y", None, None)),
    ("set_day_override", (KB, "y", 6, "12:00", None)),
    ("set_day_override", (KB, "y", 5, None, None)),
    ("set_excluded_date", (KB, "y", "2030-03-01", True)),
    ("set_excluded_date", (KB, "y", "2030-03-02", False)),
    ("remove_target", (KB, "y")), ("remove_target", (KB, "zz")),
    ("set_paused", (True,)), ("set_paused", (False,)),
]


@pytest.mark.parametrize("name, args", EDITS)
def test_every_edit_is_idempotent_and_touches_nothing_else(tmp_path, name, args):
    store, svc = editor(tmp_path)
    getattr(svc, name)(*args)
    once = copy.deepcopy(cfg(store))
    sha = store.files["config.json"][1]
    getattr(svc, name)(*args)
    assert cfg(store) == once
    assert store.files["config.json"][1] == sha  # a no-op is not written
    assert once["sources"] == js_fixture_config()["sources"]
    assert once["version"] == 1
    slug = args[0] if isinstance(args[0], str) else None
    for other, watch in js_fixture_config()["plays"].items():
        if other != slug:
            assert once["plays"][other] == watch, other


def test_unknown_fields_and_key_order_are_preserved(tmp_path):
    store, svc = editor(tmp_path)
    raw = js_fixture_config()
    raw["note"] = "elle eklendi"
    raw["plays"][KB]["targets"][0]["extra"] = 1
    store.files["config.json"] = (raw, "c1")
    svc.set_people(KB, "y", 4)
    out = cfg(store)
    assert out["note"] == "elle eklendi" and tgt(out)["extra"] == 1
    assert list(out) == list(raw)
    assert list(tgt(out)) == list(raw["plays"][KB]["targets"][0])


# Worker parity: expectations copied from worker/test/menu.test.js
def test_worker_parity_cases(tmp_path):
    store, svc = editor(tmp_path)
    svc.ensure_target("kucuk-prens", "c")
    assert tgt(cfg(store), "kucuk-prens", "c") == {
        "id": "c", "name": "Çocuk", "people": 2,
        "rule": {"days": None, "from_time": None, "to_time": None, "overrides": {},
                 "exclude_dates": []}}
    svc.ensure_target("yeni-oyun", "y")
    assert cfg(store)["plays"]["yeni-oyun"] == {
        "active": True, "tag_overrides": {}, "targets": [
            {"id": "y", "name": "Yetişkin", "people": 2,
             "rule": {"days": None, "from_time": None, "to_time": None, "overrides": {},
                      "exclude_dates": []}}]}

    def fresh():
        return editor(tmp_path)

    s, v = fresh(); v.set_days_group(KB, "y", "wd")
    assert tgt(cfg(s))["rule"]["days"] == [0, 1, 2, 3, 4]
    assert tgt(cfg(s))["rule"]["overrides"] == {"5": {"from": "14:00", "to": None}}
    s, v = fresh(); v.set_days_group(KB, "y", "we")
    assert tgt(cfg(s))["rule"]["days"] == [5, 6]
    s, v = fresh(); v.set_days_group(KB, "y", "all")
    assert tgt(cfg(s))["rule"]["days"] is None
    s, v = fresh(); v.set_day(KB, "y", 0, True)
    assert tgt(cfg(s))["rule"]["days"] == [0, 4, 5]
    s, v = fresh(); v.set_day(KB, "y", 4, False)
    assert tgt(cfg(s))["rule"]["days"] == [5]
    s, v = fresh(); v.set_days_group(KB, "y", "all"); v.set_day(KB, "y", 2, False)
    assert tgt(cfg(s))["rule"]["days"] == [0, 1, 3, 4, 5, 6]
    v.set_day(KB, "y", 2, True)
    assert tgt(cfg(s))["rule"]["days"] is None  # 7 days normalise to null
    s, v = fresh(); v.set_hours(KB, "y", 18)  # hf 18 -> from_time "18:00"
    assert tgt(cfg(s))["rule"]["from_time"] == "18:00"
    v.set_hours(KB, "y", to_time="22:00")
    assert tgt(cfg(s))["rule"]["to_time"] == "22:00"
    assert tgt(cfg(s))["rule"]["from_time"] == "18:00"  # untouched side kept
    v.set_hours(KB, "y", from_time=None)
    assert tgt(cfg(s))["rule"]["from_time"] is None
    s, v = fresh(); v.set_excluded_date(KB, "y", "2030-03-01", True)  # hx 300301:1
    assert tgt(cfg(s))["rule"]["exclude_dates"] == ["2030-03-01", "2030-03-02"]
    v.set_excluded_date(KB, "y", "2030-03-03", True)
    assert tgt(cfg(s))["rule"]["exclude_dates"] == ["2030-03-01", "2030-03-02",
                                                    "2030-03-03"]
    s, v = fresh(); v.set_excluded_date(KB, "y", "2030-03-02", False)
    assert tgt(cfg(s))["rule"]["exclude_dates"] == []
    s, v = fresh(); v.remove_target(KB, "y")
    assert cfg(s)["plays"][KB]["targets"] == []
    s, v = fresh(); v.set_paused(True)
    assert cfg(s)["paused"] is True
    s, v = fresh(); v.set_people(KB, "y", 4)
    assert tgt(cfg(s))["people"] == 4


def test_day_override_set_and_clear(tmp_path):
    store, svc = editor(tmp_path)
    svc.set_day_override(KB, "y", 6, "12:00", "16:00")
    assert tgt(cfg(store))["rule"]["overrides"] == {
        "5": {"from": "14:00", "to": None}, "6": {"from": "12:00", "to": "16:00"}}
    svc.set_day_override(KB, "y", 5, None, None)
    assert tgt(cfg(store))["rule"]["overrides"] == {"6": {"from": "12:00", "to": "16:00"}}


@pytest.mark.parametrize("name, args", [
    ("set_people", (KB, "y", 5)), ("set_people", (KB, "y", 0)),
    ("set_people", (KB, "y", True)), ("set_people", (KB, "nope", 2)),
    ("set_people", ("yok-oyun", "y", 2)),
    ("ensure_target", (KB, "x")),
    ("set_days", (KB, "y", [7])), ("set_days", (KB, "y", ["1"])),
    ("set_days_group", (KB, "y", "hafta")),
    ("set_day", (KB, "y", 9, True)),
    ("set_hours", (KB, "y", "25:00", None)), ("set_hours", (KB, "y", "7:5", None)),
    ("set_hours", (KB, "y", 24)),
    ("set_day_override", (KB, "y", 7, "12:00", None)),
    ("set_day_override", (KB, "y", 1, "1200", None)),
    ("set_excluded_date", (KB, "y", "2030-02-30", True)),
    ("set_excluded_date", (KB, "y", "300301", True)),
    ("set_active", ("", True)),
])
def test_invalid_edits_raise_and_write_nothing(tmp_path, name, args):
    store, svc = editor(tmp_path)
    before = store.files["config.json"]
    with pytest.raises(ValueError):
        getattr(svc, name)(*args)
    assert store.files["config.json"] is before


def test_invalid_resulting_config_writes_nothing(tmp_path):
    store, svc = editor(tmp_path)
    raw = js_fixture_config()
    raw["plays"]["kucuk-prens"]["targets"] = [{"id": "z", "name": "Z", "people": 9}]
    store.files["config.json"] = (raw, "bad")
    with pytest.raises(ValueError):
        svc.set_paused(True)
    assert store.files["config.json"] == (raw, "bad")


def test_concurrent_writers_both_survive(tmp_path):
    def worker(c):
        c["plays"]["kucuk-prens"]["active"] = True  # a Worker edit lands meanwhile

    store, svc = editor(tmp_path, RacyStore(worker))
    svc.set_people(KB, "y", 4)
    out = cfg(store)
    assert out["plays"]["kucuk-prens"]["active"] is True
    assert tgt(out)["people"] == 4


def test_edit_updates_cached_config(tmp_path):
    store, svc = editor(tmp_path)
    svc.set_paused(True)
    assert svc.config.paused is True
