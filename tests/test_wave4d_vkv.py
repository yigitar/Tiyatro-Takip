"""Wave 4 D+E: VKV Ford Otosan Gölcük events, reservation alerts, per-event follow."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.test_runner import Env, mkplay
from tiyatro import srcevents as S
from tiyatro.config import config_from_dict, config_to_dict
from tiyatro.fetcher import FetchError, FetchResult
from tiyatro.models import ISTANBUL, Event
from tiyatro.notify import MAX_LEN, format_message
from tiyatro.runner import _fingerprint, _prepare, plan_mode, run_once

FIX = Path(__file__).parent / "fixtures"
LIST = (FIX / "vkv_list.html").read_text(encoding="utf-8")
D540 = (FIX / "vkv_540.html").read_text(encoding="utf-8")   # opens 9 Kasım 12:00, FULL
D541 = (FIX / "vkv_541.html").read_text(encoding="utf-8")   # opens 19 Kasım 12:00
T0 = datetime(2026, 11, 15, 10, 0, tzinfo=ISTANBUL)
OPENS541 = datetime(2026, 11, 19, 12, 0, tzinfo=ISTANBUL)
URL540 = "https://www.vkvfordkultur.org.tr/EtkinlikDetay/540/ilker-gumusoluk-tek-kisilik-gosteri"
URL541 = "https://www.vkvfordkultur.org.tr/EtkinlikDetay/541/ceviz-adam-muzikali"


def without_opening(html: str) -> str:
    """The detail page after the "... rezervasyona açılacaktır" sentence is gone."""
    import html as h
    import re
    return re.sub(r"[^<>]*rezervasyona açılacaktır[.]?", "", h.unescape(html))


def cfg(**extra):
    extra.setdefault("portals", [])
    return config_from_dict(extra)


class Web:
    """Fake VKV site (list + detail pages)."""

    def __init__(self, list_html=LIST, d540=D540, d541=D541):
        self.pages = {S.VKV_LIST_URL: list_html, URL540: d540, URL541: d541}
        self.calls: list[str] = []
        self.fail: set[str] = set()

    def fetch(self, url):
        self.calls.append(url)
        if url in self.fail:
            raise FetchError(f"HTTP 500: {url}")
        if url not in self.pages:
            raise FetchError(f"HTTP 404: {url}")
        return FetchResult(self.pages[url], 200, 1)


# ---- parsing --------------------------------------------------------------------------

def test_list_fixture_cards():
    got = S.parse_vkv_list(LIST)
    assert [(l.id, l.title, l.kind) for l in got] == [
        ("vkv:540", "İlker Gümüşoluk - Tek Kişilik Gösteri", "Tiyatro"),
        ("vkv:541", "Ceviz Adam Müzikali", "Çocuk Tiyatrosu")]
    assert got[0].start == datetime(2026, 11, 13, 20, 0, tzinfo=ISTANBUL)
    assert got[1].start == datetime(2026, 11, 28, 11, 0, tzinfo=ISTANBUL)
    assert got[1].url == URL541 and not any(l.follow_default for l in got)   # wave 6: OFF


def test_only_theatre_categories_are_tracked():
    card = LIST.replace('class="kat">Çocuk Tiyatrosu</p>', 'class="kat">Atölye</p>')
    got = S.parse_vkv_list(card)
    assert [S.vkv_tracked(l) for l in got] == [True, False]   # tracked categories; follow default is OFF for all
    assert [l.follow_default for l in got] == [False, False]
    scan = S.scan_vkv(Web(card).fetch, T0)
    assert scan.listings == []   # 540 is over, the workshop is not tracked: nothing is read
    scan = S.scan_vkv(Web(card).fetch, T0 - timedelta(days=3))
    assert [l.id for l in scan.listings] == ["vkv:540"]  # the "Atölye" card is ignored


@pytest.mark.parametrize("bad", ["", None, "<html></html>", "x" * 3000, "<div class=\"event-cont\">"])
def test_list_garbage(bad):
    assert S.parse_vkv_list(bad) == []


def test_detail_opening_and_full_flags():
    d = S.parse_vkv_detail(D540, datetime(2026, 11, 13, 20, 0, tzinfo=ISTANBUL))
    assert d.opens_at == datetime(2026, 11, 9, 12, 0, tzinfo=ISTANBUL) and d.full is True
    d = S.parse_vkv_detail(D541, datetime(2026, 11, 28, 11, 0, tzinfo=ISTANBUL))
    assert d.opens_at == OPENS541 and d.opens_pending is True and d.full is False
    # neither a stated opening nor "dolmuştur": unknown
    plain = without_opening(D541)
    d = S.parse_vkv_detail(plain, datetime(2026, 11, 28, 11, 0, tzinfo=ISTANBUL))
    assert (d.opens_at, d.full) == (None, False)
    assert S.parse_vkv_detail("", None) == S.VkvDetail()


def test_detail_sentence_variants():
    start = datetime(2026, 11, 28, 11, 0, tzinfo=ISTANBUL)
    for text in ("<p>Rezervasyon 19 Kasım Perşembe günü saat 12:00’da açılacaktır.</p>",
                 "<p>19 Kasım Perşembe günü saat 12.00'da rezervasyona açılacaktır</p>"):
        assert S.parse_vkv_detail(text, start).opens_at == OPENS541
    # an opening "after" the event itself is implausible and ignored
    assert S.parse_vkv_detail("<p>Rezervasyon 30 Kasım Pazartesi günü saat 12:00'da "
                              "açılacaktır.</p>", start).opens_at is None


# ---- scan -----------------------------------------------------------------------------

def test_scan_reads_list_then_details_of_tracked_future_events():
    web = Web()
    scan = S.scan_vkv(web.fetch, T0 - timedelta(days=3))   # 540 still ahead
    assert scan.ok and scan.error is None
    assert web.calls == [S.VKV_LIST_URL, URL540, URL541]
    by = {l.id: l for l in scan.listings}
    assert by["vkv:540"].full is True and by["vkv:541"].opens_at == OPENS541
    scan = S.scan_vkv(Web().fetch, T0)                       # 540 is over: not read
    assert [l.id for l in scan.listings] == ["vkv:541"]


def test_scan_failures():
    web = Web()
    web.fail = {S.VKV_LIST_URL}
    assert (S.scan_vkv(web.fetch, T0).ok, S.scan_vkv(web.fetch, T0).error) == (False, "FetchError")
    web = Web(list_html="<html>captcha</html>")
    assert S.scan_vkv(web.fetch, T0).error == "ValueError"
    empty_month = LIST.split('<div class="event-cont">')[0] + "</div></div></div>"
    ok = S.scan_vkv(Web(empty_month).fetch, T0)
    assert ok.ok and ok.listings == []                        # a calendar with no event
    web = Web()
    web.fail = {URL541}
    scan = S.scan_vkv(web.fetch, T0)
    assert (scan.ok, scan.error) == (False, "DetailFetch")    # every detail failed
    web = Web()
    web.fail = {URL540}
    scan = S.scan_vkv(web.fetch, T0 - timedelta(days=3))
    assert scan.ok and scan.error == "DetailFetch(1)"


# ---- folding: the alert chain ------------------------------------------------------------

def mk(eid="vkv:541", full=None, opens=OPENS541, pending=True, kind="Çocuk Tiyatrosu",
       detail=True, start=None, default=True):
    return S.Listing(S.VKV, eid, "Ceviz Adam Müzikali", kind,
                     start or datetime(2026, 11, 28, 11, 0, tzinfo=ISTANBUL), "VKV",
                     URL541, opens, pending if opens else None, full, detail, default)


def base():
    return _prepare({"plays": {}, "meta": {}, "emitted": {}})


def fold(st, listings, now, config=None):
    return S.fold_listings(st, config or cfg(), S.VKV, listings, now)


def kinds(events):
    return [e.kind for e in events]


def test_full_alert_chain_for_one_event():
    st = base()
    other = mk("vkv:1", opens=None)
    assert fold(st, [other], T0) == []                                   # baseline
    ev = fold(st, [other, mk()], T0 + timedelta(minutes=5))
    assert kinds(ev) == ["SRC_NEW"]                                      # announced + opening shown
    assert ev[0].payload["opens_at"] == OPENS541.isoformat()
    assert fold(st, [other, mk()], T0 + timedelta(minutes=10)) == []
    # 1 day before
    ev = fold(st, [other, mk()], OPENS541 - timedelta(hours=23, minutes=30))
    assert kinds(ev) == ["SRC_DAY_BEFORE"] and ev[0].key.startswith("SRC_DAY:vkv:541:")
    assert fold(st, [other, mk()], OPENS541 - timedelta(hours=20)) == []
    # 10 minutes before
    ev = fold(st, [other, mk()], OPENS541 - timedelta(minutes=10))
    assert kinds(ev) == ["SRC_SOON"]
    # the opening time arrives while the page still says "açılacaktır": alert anyway
    ev = fold(st, [other, mk()], OPENS541 + timedelta(seconds=30))
    assert kinds(ev) == ["SRC_OPENED"] and "güncellenmemiş" in ev[0].payload["detail"]
    assert fold(st, [other, mk()], OPENS541 + timedelta(minutes=2)) == []
    # full, then re-opened
    assert fold(st, [other, mk(full=True)], OPENS541 + timedelta(minutes=5)) == []
    ev = fold(st, [other, mk(full=False)], OPENS541 + timedelta(minutes=9))
    assert kinds(ev) == ["SRC_REOPENED"] and ev[0].key == "SRC_REOPENED:vkv:541:1"
    assert fold(st, [other, mk(full=False)], OPENS541 + timedelta(minutes=12)) == []
    fold(st, [other, mk(full=True)], OPENS541 + timedelta(minutes=14))
    ev = fold(st, [other, mk(full=False)], OPENS541 + timedelta(minutes=16))
    assert ev[0].key == "SRC_REOPENED:vkv:541:2"


def test_page_stops_announcing_the_opening_means_it_opened():
    st = base()
    fold(st, [mk("vkv:1", opens=None)], T0)
    fold(st, [mk("vkv:1", opens=None), mk()], T0 + timedelta(minutes=1))
    gone = mk(opens=None)       # detail read, sentence gone
    ev = fold(st, [mk("vkv:1", opens=None), gone], OPENS541 + timedelta(seconds=45))
    assert kinds(ev) == ["SRC_OPENED"] and "detail" not in ev[0].payload
    assert st["source_events"]["vkv:541"]["opens_at"] == OPENS541.isoformat()
    assert fold(st, [mk("vkv:1", opens=None), gone], OPENS541 + timedelta(minutes=2)) == []


def test_sentence_vanishing_before_the_stated_time_is_an_early_opening():
    st = base()
    fold(st, [mk("vkv:1", opens=None)], T0)
    fold(st, [mk("vkv:1", opens=None), mk()], T0 + timedelta(minutes=1))
    ev = fold(st, [mk("vkv:1", opens=None), mk(opens=None)], OPENS541 - timedelta(hours=3))
    assert kinds(ev) == ["SRC_OPENED"] and "açılış duyurusu kalktı" in ev[0].payload["detail"]
    # the time itself arrives later: the same key, so no second alert
    assert fold(st, [mk("vkv:1", opens=None), mk(opens=None)], OPENS541 + timedelta(minutes=1)) == []


def test_opened_detail_text_after_the_time_is_a_plain_opened():
    st = base()
    fold(st, [mk("vkv:1", opens=None)], T0)
    fold(st, [mk("vkv:1", opens=None), mk()], T0 + timedelta(minutes=1))
    ev = fold(st, [mk("vkv:1", opens=None), mk(pending=False)], OPENS541 + timedelta(seconds=45))
    assert kinds(ev) == ["SRC_OPENED"] and "detail" not in ev[0].payload


def test_opening_announced_later_for_a_known_event():
    st = base()
    fold(st, [mk("vkv:1", opens=None), mk(opens=None)], T0)
    ev = fold(st, [mk("vkv:1", opens=None), mk()], T0 + timedelta(minutes=5))
    assert kinds(ev) == ["SRC_OPENS"]
    assert fold(st, [mk("vkv:1", opens=None), mk()], T0 + timedelta(minutes=10)) == []


def test_no_alerts_when_the_event_is_switched_off_and_default_on_for_theatre_only():
    st = base()
    off = cfg(event_follow={"vkv:541": False})
    fold(st, [mk("vkv:1", opens=None)], T0, off)
    ev = fold(st, [mk("vkv:1", opens=None), mk()], T0 + timedelta(minutes=5), off)
    assert ev == []
    for now in (OPENS541 - timedelta(hours=23), OPENS541 - timedelta(minutes=5),
                OPENS541 + timedelta(minutes=1)):
        assert fold(st, [mk("vkv:1", opens=None), mk()], now, off) == []
    assert st["source_events"]["vkv:541"]["opens_at"]       # still tracked + listed
    assert S.effective_follow(cfg(), "vkv:541", True) is True
    assert S.effective_follow(cfg(), "x", False) is False
    assert S.effective_follow(cfg(event_follow={"x": True}), "x", False) is True


def test_switching_back_on_resumes_alerts():
    st = base()
    fold(st, [mk("vkv:1", opens=None), mk()], T0)
    off = cfg(event_follow={"vkv:541": False})
    assert fold(st, [mk()], OPENS541 - timedelta(minutes=5), off) == []
    ev = fold(st, [mk()], OPENS541 - timedelta(minutes=4), cfg())
    assert kinds(ev) == ["SRC_SOON"]


def test_reservation_text_and_views():
    st = base()
    fold(st, [mk("vkv:1", opens=None, start=T0 + timedelta(days=1)), mk(), mk("vkv:2", full=True, opens=None)], T0)
    rows = {r["id"]: r for r in S.event_views(st, cfg(), T0)}
    assert rows["vkv:541"]["reservation"] == "19 Kasım 12:00'da açılacak"
    assert rows["vkv:2"]["reservation"] == "Doldu"
    assert rows["vkv:1"]["reservation"] == "Duyuru bekleniyor"
    assert rows["vkv:541"]["followed"] is True and rows["vkv:541"]["audience"] == "Çocuk"
    assert rows["vkv:541"]["source_name"] == "VKV Gölcük" and rows["vkv:541"]["genre"] == "Müzikal"
    off = {r["id"]: r for r in S.event_views(st, cfg(event_follow={"vkv:541": False}), T0)}
    assert off["vkv:541"]["followed"] is False
    after = {r["id"]: r for r in S.event_views(st, cfg(), OPENS541 + timedelta(minutes=1))}
    assert after["vkv:541"]["reservation"] == "Açılmış olabilir"       # sentence still pending


# ---- burst windows ---------------------------------------------------------------------

def test_burst_window_and_plan_mode():
    st = base()
    fold(st, [mk("vkv:1", opens=None), mk()], T0)
    c = cfg()
    assert S.burst_ids(st, c, OPENS541 - timedelta(minutes=2)) == []
    assert S.burst_ids(st, c, OPENS541 - timedelta(seconds=50)) == ["vkv:541"]
    assert S.burst_ids(st, c, OPENS541 + timedelta(minutes=29)) == ["vkv:541"]
    assert S.burst_ids(st, c, OPENS541 + timedelta(minutes=31)) == []
    assert S.burst_ids(st, cfg(event_follow={"vkv:541": False}), OPENS541) == []
    st["source_events"]["vkv:541"]["full"] = True
    assert S.burst_ids(st, c, OPENS541) == []                              # full: nothing to open
    st["source_events"]["vkv:541"]["full"] = False
    mode, until = plan_mode(st, c, OPENS541 + timedelta(minutes=1))
    assert mode == "burst" and until == OPENS541 + timedelta(minutes=30)
    assert plan_mode(st, c, OPENS541 - timedelta(hours=1)) == ("normal", None)


def test_burst_check_is_throttled_to_about_a_minute():
    rec = {"last_detail_at": T0.isoformat()}
    assert S.due_for_burst_check(rec, T0 + timedelta(seconds=30)) is False
    assert S.due_for_burst_check(rec, T0 + timedelta(seconds=56)) is True
    assert S.due_for_burst_check({}, T0) is True


# ---- runner ---------------------------------------------------------------------------

class VkvEnv(Env):
    def __init__(self, monkeypatch, who="github"):
        super().__init__(monkeypatch, [mkplay("zz")],
                         {"portals": [], "event_follow": {"vkv:541": True}}, who=who)  # wave 6: explicit
        self.vkv = Web()

    def go(self, now, **kw):
        kw.setdefault("portal_fetch_fn", self.vkv.fetch)
        return self.run(now, **kw)


def test_run_once_full_flow_with_contact_block(monkeypatch):
    env = VkvEnv(monkeypatch)
    env.go(T0)                                                 # baseline
    st = env.saved_state()
    assert st["sources"]["vkv"]["ok"] and st["sources"]["vkv"]["items_count"] == 1
    assert set(st["source_events"]) >= {"vkv:541"}
    assert st["source_events"]["vkv:541"]["opens_at"] == OPENS541.isoformat()
    env.tg.sent.clear()
    env.go(OPENS541 - timedelta(hours=23))
    assert len(env.tg.sent) == 1
    msg = env.tg.sent[0]
    assert "VKV Gölcük: 🔔 yarın rezervasyon açılıyor" in msg and "Ceviz Adam Müzikali" in msg
    for needle in ("(0262) 435 6700", "info@vkvfordkultur.org.tr", URL541, "28 Kasım Cumartesi 11:00",
                   "19 Kasım Perşembe 12:00"):
        assert needle in msg, needle
    env.go(OPENS541 - timedelta(hours=22))
    assert len(env.tg.sent) == 1


def test_run_once_burst_polls_the_detail_page_and_alerts_at_opening(monkeypatch):
    env = VkvEnv(monkeypatch)
    env.go(T0)
    env.tg.sent.clear()
    env.vkv.calls.clear()
    t = OPENS541 - timedelta(seconds=40)
    res = env.go(t, only_slugs=[])
    assert env.vkv.calls == [URL541] and res.mode == "burst"
    assert len(env.tg.sent) == 1 and "10 dk sonra rezervasyon açılıyor" in env.tg.sent[0]
    # (a) the page flips: sentence gone -> opened
    env.vkv.pages[URL541] = without_opening(D541)
    env.vkv.calls.clear()
    env.go(t + timedelta(seconds=20), only_slugs=[])
    assert env.vkv.calls == []                                 # throttled (< 55 s)
    env.go(OPENS541 + timedelta(seconds=20), only_slugs=[])
    assert env.vkv.calls == [URL541]
    assert len(env.tg.sent) == 2 and "rezervasyon açıldı" in env.tg.sent[-1]
    assert "(0262) 435 6700" in env.tg.sent[-1]
    n = len(env.tg.sent)
    env.go(OPENS541 + timedelta(minutes=2), only_slugs=[])
    assert len(env.tg.sent) == n                               # once


def test_burst_does_not_run_without_source_fetcher_or_outside_the_window(monkeypatch):
    env = VkvEnv(monkeypatch)
    env.go(T0)
    env.vkv.calls.clear()
    env.run(OPENS541, only_slugs=[])                           # no fetcher injected
    env.go(OPENS541 - timedelta(hours=3), only_slugs=[])       # outside the window
    assert env.vkv.calls == []


def test_vkv_failure_keeps_biletinial_and_status_shows_it(monkeypatch):
    env = VkvEnv(monkeypatch)
    env.vkv.fail = {S.VKV_LIST_URL}
    res = env.go(T0)
    assert res.scanned == 1
    rec = env.saved_state()["sources"]["vkv"]
    assert rec["ok"] is False and rec["error"] == "FetchError"


def test_paused_discards_vkv_alerts(monkeypatch):
    env = VkvEnv(monkeypatch)
    env.store.files["config.json"] = ({"paused": True, "portals": []}, "c")
    env.go(T0)
    env.tg.sent.clear()
    env.go(OPENS541 - timedelta(hours=23))
    assert env.tg.sent == []


def test_fingerprint_ignores_detail_check_times():
    st = base()
    fold(st, [mk()], T0)
    fp = _fingerprint(st)
    fold(st, [mk()], T0 + timedelta(minutes=1))
    assert _fingerprint(st) == fp


# ---- notification text ------------------------------------------------------------------

def test_every_vkv_notification_kind_has_the_contact_block():
    st = base()
    fold(st, [mk("vkv:1", opens=None)], T0)
    seen = set()
    for ls, now in ((mk(), T0 + timedelta(minutes=1)),
                    (mk(), OPENS541 - timedelta(hours=23)),
                    (mk(), OPENS541 - timedelta(minutes=5)),
                    (mk(), OPENS541 + timedelta(seconds=10)),
                    (mk(full=True), OPENS541 + timedelta(minutes=3)),
                    (mk(full=False), OPENS541 + timedelta(minutes=6))):
        for ev in fold(st, [ls], now):
            seen.add(ev.kind)
            [text] = format_message([ev], {}, cfg())
            assert "(0262) 435 6700" in text and "info@vkvfordkultur.org.tr" in text
            assert "çevrimiçi rezervasyon bağlantısı yok" in text and URL541 in text
            assert len(text) <= MAX_LEN
    assert seen == {"SRC_NEW", "SRC_DAY_BEFORE", "SRC_SOON", "SRC_OPENED", "SRC_REOPENED"}


def test_contact_data_lives_in_one_constant():
    assert S.VKV_CONTACT_PHONE == "(0262) 435 6700"
    assert S.VKV_CONTACT_EMAIL == "info@vkvfordkultur.org.tr"
    import re
    src = Path(S.__file__).read_text(encoding="utf-8")
    assert len(re.findall(r"435 6700", src)) == 1


# ---- E: follow toggles in the config -----------------------------------------------------

def test_event_follow_round_trips_and_is_validated():
    c = config_from_dict({"event_follow": {"vkv:541": False, "bk:x": True}})
    assert c.event_follow == {"vkv:541": False, "bk:x": True}
    assert config_to_dict(c)["event_follow"] == {"vkv:541": False, "bk:x": True}
    assert config_from_dict({}).event_follow == {}
    for bad in ({"event_follow": []}, {"event_follow": {"a": "yes"}}, {"event_follow": {"": True}},
                {"event_follow": {1: True}}):
        with pytest.raises(ValueError):
            config_from_dict(bad)


def test_configedit_set_event_follow_is_a_set_operation():
    from pc import configedit
    raw = {"plays": {}, "sources": [], "paused": False, "version": 1}
    configedit.set_event_follow(raw, "vkv:541", False)
    assert raw["event_follow"] == {"vkv:541": False}
    once = dict(raw["event_follow"])
    configedit.set_event_follow(raw, "vkv:541", False)
    assert raw["event_follow"] == once                     # idempotent
    configedit.set_event_follow(raw, "vkv:541", True)
    configedit.set_event_follow(raw, "bk:x", False)
    assert raw["event_follow"] == {"vkv:541": True, "bk:x": False}
    assert config_from_dict(raw).event_follow == raw["event_follow"]
    for bad in ("", None, "x" * 121, 5):
        with pytest.raises(ValueError):
            configedit.set_event_follow(raw, bad, True)


def test_service_follow_toggle_edits_config_logs_and_changes_alerts(monkeypatch, tmp_path):
    from pc.service import Service
    from tests.test_pc_service import make, tick_at
    from tiyatro.claims import ClaimStore
    e = make(monkeypatch, tmp_path)
    e.store.files["config.json"] = ({"portals": [], "event_follow": {"vkv:541": True}}, "cfg")
    web = Web()
    e.svc = Service(e.store, e.tg, ClaimStore(e.store, "pc", e.clock), clock=e.clock,
                    fetch_fn=e.web.fetch, sleep=lambda s: None, local_dir=tmp_path,
                    rand=lambda a, b: 0.0, portal_fetch_fn=web.fetch)
    tick_at(e, T0)
    rows = {r["id"]: r for r in e.svc.event_views(T0)}
    assert rows["vkv:541"]["followed"] is True
    e.svc.set_event_follow("vkv:541", False)
    assert e.store.files["config.json"][0]["event_follow"] == {"vkv:541": False}
    assert e.svc.event_views(T0)[0]["followed"] is False
    logged = [x for x in e.svc.activity.load() if "etkinlik takibi kapatıldı" in x["title"]]
    assert logged and "Ceviz Adam Müzikali" in logged[0]["title"]
    # a second identical call writes and logs nothing more
    before = len(e.svc.activity.load())
    e.svc.set_event_follow("vkv:541", False)
    assert len(e.svc.activity.load()) == before
    e.svc.set_event_follow("vkv:541", True)
    assert e.svc.event_views(T0)[0]["followed"] is True
