"""Wave 4 C: Başiskele municipality program articles (RSS -> article -> events)."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.test_runner import Env, mkplay
from tiyatro import srcevents as S
from tiyatro.config import config_from_dict
from tiyatro.fetcher import FetchError, FetchResult, decode_body
from tiyatro.models import ISTANBUL, Event
from tiyatro.notify import MAX_LEN, format_message
from tiyatro.runner import _fingerprint, _prepare

FIX = Path(__file__).parent / "fixtures"
RSS = (FIX / "bk_rss2.xml").read_text(encoding="utf-8-sig")
ART = decode_body((FIX / "bk_art.html").read_bytes(), "text/html; charset=iso-8859-9")
NOV3 = datetime(2025, 11, 3, 10, 0, tzinfo=ISTANBUL)
PUB = datetime(2025, 10, 31, 12, 0, tzinfo=ISTANBUL)
ARTICLE_URL = ("https://www.basiskele.bel.tr/haber/1/12879/"
               "kasim-ayinda-da-dolu-dolu-kultursanat.aspx")


def cfg(**extra):
    return config_from_dict(extra)


# ---- RSS ------------------------------------------------------------------------------

def test_rss_items_and_program_filter():
    items = S.parse_rss(RSS)
    assert len(items) == 20 and items[0].link.startswith("https://www.basiskele.bel.tr/haber/")
    assert items[0].published is not None
    prog = S.program_items(items)
    assert [i.title for i in prog] == ["Kasım Ayında da Dolu Dolu Kültür-Sanat"]
    assert prog[0].link == ARTICLE_URL


@pytest.mark.parametrize("title,expected", [
    ("Kasım Ayında da Dolu Dolu Kültür-Sanat", True),
    ("Aralık Ayı Etkinlik Programı Açıklandı", True),
    ("Ocak Kültür Sanat Takvimi", True),
    ("Başkan Özlü Sabah Namazında Gençlerle Buluştu", False),
    ("Kültür Sanat Haftası Başladı", False),       # no month
    ("Kasım Ayında Fidan Dikildi", False),          # month but not a program
])
def test_is_program_title(title, expected):
    assert S.is_program_title(title) is expected


@pytest.mark.parametrize("bad", ["", "garbage", "<rss><channel></channel></rss>", None])
def test_rss_garbage_gives_no_items(bad):
    assert S.parse_rss(bad) == []


# ---- article --------------------------------------------------------------------------

def test_article_fixture_events():
    events, opens, pending = S.parse_article(ART, ARTICLE_URL, PUB)
    assert opens is None and pending is False       # the article states no opening time
    got = [(e.start.strftime("%d.%m %H:%M"), e.title, e.kind, e.venue) for e in events]
    assert len(got) == 13
    by_title = {(e.title, e.start.strftime("%d.%m %H:%M")): e for e in events}
    e = by_title[("Cesur İtfaiyeci", "12.11 19:00")]
    assert (e.kind, e.venue) == ("Animasyon Filmi", "Türkiye Yüzyılı Gençlik Merkezi")
    assert e.start.year == 2025                       # weekday (Çarşamba) fixes the year
    assert e.follow_default is False
    e = by_title[("Hiç Kimsenin Öyküsü", "25.11 20:00")]
    assert e.kind == "Yetişkin Tiyatrosu" and e.follow_default is False   # wave 6: default OFF
    # two sessions on one day are two events
    assert ("Niloya Sürpriz Yumurta Müzikali", "14.11 15:30") in by_title
    assert ("Niloya Sürpriz Yumurta Müzikali", "14.11 19:00") in by_title
    assert by_title[("İki Teker Bir Rüya", "20.11 11:00")].kind == "Çocuk Tiyatrosu"
    # narrative lead paragraph: kinds are taken from the sentence ("adlı çocuk tiyatrosu")
    cop = by_title[("Çöp Atlas", "04.11 14:00")]
    assert cop.kind == "Çocuk Tiyatrosu"
    dile = by_title[("Dile Benden Ne Dilersen", "07.11 19:30")]
    assert dile.kind == "Yetişkin Tiyatrosu"          # coordinated with "Masanın Altında"
    assert by_title[("Masanın Altında", "09.11 18:00")].kind == "Yetişkin Tiyatrosu"
    # workshop / talk lines are listed but not followed by default
    assert by_title[("Evlilik Okulu", "22.11 14:00")].follow_default is False
    assert by_title[("Kitaphane Kültür Söyleşileri", "26.11 20:00")].venue == "Kitaphane"


def test_event_ids_are_stable_and_unique():
    a, _o, _p = S.parse_article(ART, ARTICLE_URL, PUB)
    b, _o, _p = S.parse_article(ART, ARTICLE_URL + "?x=1", PUB)
    assert [e.id for e in a] == [e.id for e in b]
    assert len({e.id for e in a}) == len(a)
    assert a[0].id.startswith("bk:2025")


def test_year_is_resolved_by_weekday_and_falls_back():
    line = "<p>12 Aralık Cuma – 20.00 – Deneme Oyunu (Yetişkin Tiyatrosu) – Salon</p>"
    ev, _o, _p = S.parse_article(line, "u", datetime(2026, 11, 20, tzinfo=ISTANBUL))
    assert ev[0].start == datetime(2025, 12, 12, 20, 0, tzinfo=ISTANBUL)
    ev, _o, _p = S.parse_article("<p>13 Kasım Cuma – 20.00 – X (Tiyatro) – Salon</p>", "u",
                                 datetime(2026, 11, 1, tzinfo=ISTANBUL))
    assert ev[0].start.year == 2026
    ev, _o, _p = S.parse_article("<p>31 Kasım Cuma – 20.00 – X – Salon</p>", "u", PUB)
    assert ev == []                                   # impossible date: skipped


def test_article_robustness():
    for bad in ("", "<<<", "<p>", None, "x" * 5000):
        assert S.parse_article(bad, "u", PUB)[0] == []
    ev, _o, _p = S.parse_article("<p>12 Kasım Çarşamba &ndash; 19.00 &ndash; A (Film)</p>", "u", PUB)
    assert ev[0].venue == "" and ev[0].title == "A"   # no venue token


def test_opening_sentence_is_parsed_with_time_only():
    years = [2026]
    at, pending = S.parse_opening(
        "Rezervasyonlar 9 Kasım Pazartesi günü saat 10.00'da başlayacaktır.", years)
    assert at == datetime(2026, 11, 9, 10, 0, tzinfo=ISTANBUL) and pending is True
    at, pending = S.parse_opening("Biletler 12 Kasım Perşembe saat 09:30’da satışa açıldı.",
                                  years)
    assert at == datetime(2026, 11, 12, 9, 30, tzinfo=ISTANBUL) and pending is False
    # a date without a clock time is not an opening time we can remind about
    assert S.parse_opening("Rezervasyonlar 9 Kasım Pazartesi başlayacak.", years) == (None, False)
    assert S.parse_opening("Etkinlik 10 Kasım saat 20.00'de başlıyor.", years) == (None, False)
    assert S.parse_opening("", years) == (None, False)


def test_article_with_stated_opening_applies_to_events():
    html = ('<p>12 Kasım Perşembe – 20.00 – Oyun (Çocuk Tiyatrosu) – Salon</p>'
            "<p>Rezervasyonlar 9 Kasım Pazartesi günü saat 10.00'da başlayacaktır.</p>")
    ev, opens, pending = S.parse_article(html, "u", datetime(2026, 11, 1, tzinfo=ISTANBUL))
    assert opens == datetime(2026, 11, 9, 10, 0, tzinfo=ISTANBUL) and pending is True
    assert len(ev) == 1


# ---- scan -----------------------------------------------------------------------------

class Web:
    def __init__(self, rss=RSS, article=ART):
        self.rss, self.article, self.calls = rss, article, []
        self.fail: set[str] = set()

    def fetch(self, url):
        self.calls.append(url)
        if url in self.fail or any(f in url for f in self.fail):
            raise FetchError(f"HTTP 500: {url}")
        if url == S.BASISKELE_RSS:
            return FetchResult(self.rss, 200, 1)
        if url == ARTICLE_URL:
            return FetchResult(self.article, 200, 1)
        raise FetchError(f"HTTP 404: {url}")


def test_scan_reads_rss_then_the_program_article():
    web = Web()
    scan = S.scan_basiskele(web.fetch, None, NOV3)
    assert scan.ok and scan.error is None
    assert web.calls == [S.BASISKELE_RSS, ARTICLE_URL]
    assert len(scan.listings) == 13 and ARTICLE_URL in scan.articles
    assert all(l.detail_ok for l in scan.listings)


def test_scan_remembers_articles_that_rolled_out_of_the_feed():
    web = Web()
    t0 = datetime(2026, 11, 1, 10, 0, tzinfo=ISTANBUL)   # after the feed item's pubDate
    first = S.scan_basiskele(web.fetch, None, t0)
    web.rss = RSS.replace("Kasım Ayında da Dolu Dolu Kültür-Sanat", "Başka Haber")
    again = S.scan_basiskele(web.fetch, first.articles, t0 + timedelta(days=2))
    assert again.ok and len(again.listings) == 13   # still read through the cache
    far = S.scan_basiskele(web.fetch, first.articles, t0 + timedelta(days=90))
    assert far.listings == [] and far.articles == {}  # expired


def test_scan_failures_are_short_and_secret_free():
    web = Web()
    web.fail = {S.BASISKELE_RSS}
    scan = S.scan_basiskele(web.fetch, None, NOV3)
    assert (scan.ok, scan.error) == (False, "FetchError")
    web = Web(rss="<html>not a feed</html>")
    scan = S.scan_basiskele(web.fetch, None, NOV3)
    assert (scan.ok, scan.error) == (False, "ValueError")
    web = Web()
    web.fail = {ARTICLE_URL}
    scan = S.scan_basiskele(web.fetch, None, NOV3)
    assert (scan.ok, scan.error) == (False, "ArticleFetch")


def test_feed_without_a_program_article_is_ok_and_empty():
    web = Web(rss=RSS.replace("Kasım Ayında da Dolu Dolu Kültür-Sanat", "Başka Haber"))
    scan = S.scan_basiskele(web.fetch, None, NOV3)
    assert scan.ok and scan.listings == [] and web.calls == [S.BASISKELE_RSS]


# ---- folding: announcements, reminders, follow ------------------------------------------

def listing(eid="bk:t1", title="Oyun", kind="Çocuk Tiyatrosu", start=None, opens=None,
            pending=True, default=True, source="basiskele", detail=True, full=None):
    return S.Listing(source, eid, title, kind, start or NOV3 + timedelta(days=5), "Salon",
                     "https://example.test/x", opens, pending if opens else None, full,
                     detail, default)


def fold(state, listings, now, config=None, source="basiskele"):
    return S.fold_listings(state, config or cfg(), source, listings, now)


def base_state():
    return _prepare({"plays": {}, "meta": {}, "emitted": {}})


def test_first_scan_is_a_silent_baseline_then_new_events_are_announced_once():
    st = base_state()
    assert fold(st, [listing("bk:a")], NOV3) == []                 # baseline
    ev = fold(st, [listing("bk:a"), listing("bk:b", "Yeni")], NOV3 + timedelta(minutes=5))
    assert [(e.kind, e.key) for e in ev] == [("SRC_NEW", "SRC_NEW:bk:b")]
    assert ev[0].payload["title"] == "Yeni" and ev[0].payload["source"] == "basiskele"
    assert fold(st, [listing("bk:a"), listing("bk:b", "Yeni")], NOV3 + timedelta(minutes=10)) == []
    assert set(st["source_events"]) == {"bk:a", "bk:b"}


def test_non_theatre_events_are_listed_but_not_announced_by_default():
    st = base_state()
    fold(st, [listing("bk:a")], NOV3)
    ev = fold(st, [listing("bk:a"), listing("bk:w", "Atölye", "", default=False)],
              NOV3 + timedelta(minutes=5))
    assert ev == [] and "bk:w" in st["source_events"]
    # the user can switch it on
    ev = fold(st, [listing("bk:a"), listing("bk:w", "Atölye", "", default=False,
                                            opens=NOV3 + timedelta(days=3))],
              NOV3 + timedelta(minutes=10), cfg(event_follow={"bk:w": True}))
    assert [e.kind for e in ev] == ["SRC_OPENS"]


def test_follow_off_means_no_alerts_but_the_event_stays_listed():
    st = base_state()
    fold(st, [listing("bk:a")], NOV3)
    off = cfg(event_follow={"bk:b": False})
    ev = fold(st, [listing("bk:a"), listing("bk:b", opens=NOV3 + timedelta(minutes=5))],
              NOV3 + timedelta(minutes=1), off)
    assert ev == []
    ev = fold(st, [listing("bk:a"), listing("bk:b", opens=NOV3 + timedelta(minutes=5))],
              NOV3 + timedelta(minutes=6), off)
    assert ev == [] and st["source_events"]["bk:b"]["opens_at"]


def test_reminders_only_when_an_opening_time_is_stated():
    st = base_state()
    start = NOV3 + timedelta(days=6)
    fold(st, [listing(start=start)], NOV3)                       # no opening stated
    for minutes in (0, 60, 24 * 60, 5 * 24 * 60):
        assert fold(st, [listing(start=start)], NOV3 + timedelta(minutes=minutes)) == []

    st = base_state()
    opens = NOV3 + timedelta(days=2)
    fold(st, [listing(start=start, opens=opens)], NOV3)          # baseline (silent)
    assert fold(st, [listing(start=start, opens=opens)], NOV3 + timedelta(hours=10)) == []
    ev = fold(st, [listing(start=start, opens=opens)], opens - timedelta(hours=23))
    assert [e.kind for e in ev] == ["SRC_DAY_BEFORE"]
    assert fold(st, [listing(start=start, opens=opens)], opens - timedelta(hours=22)) == []
    ev = fold(st, [listing(start=start, opens=opens)], opens - timedelta(minutes=9))
    assert [e.kind for e in ev] == ["SRC_SOON"]
    ev = fold(st, [listing(start=start, opens=opens)], opens + timedelta(minutes=1))
    assert [e.kind for e in ev] == ["SRC_OPENED"]
    assert "güncellenmemiş" in ev[0].payload["detail"]            # page still says "açılacak"
    assert fold(st, [listing(start=start, opens=opens)], opens + timedelta(minutes=6)) == []


def test_changed_opening_time_is_announced_and_reminded_again():
    st = base_state()
    start = NOV3 + timedelta(days=8)
    fold(st, [listing(start=start)], NOV3)
    ev = fold(st, [listing(start=start, opens=NOV3 + timedelta(days=3))], NOV3 + timedelta(minutes=5))
    assert [e.kind for e in ev] == ["SRC_OPENS"]
    ev = fold(st, [listing(start=start, opens=NOV3 + timedelta(days=4))], NOV3 + timedelta(minutes=10))
    assert [e.kind for e in ev] == ["SRC_OPENS"]


def test_past_events_are_bookkeeping_only():
    st = base_state()
    old = NOV3 - timedelta(days=1)
    fold(st, [listing("bk:a", start=NOV3 + timedelta(days=9))], NOV3)
    ev = fold(st, [listing("bk:a", start=NOV3 + timedelta(days=9)),
                   listing("bk:old", start=old)], NOV3 + timedelta(minutes=5))
    assert ev == []
    assert S.event_views(st, cfg(), NOV3 + timedelta(minutes=5))[0]["id"] == "bk:a"


def test_old_records_are_pruned():
    st = base_state()
    fold(st, [listing("bk:a", start=NOV3 + timedelta(days=1))], NOV3)
    fold(st, [], NOV3 + timedelta(days=10))
    assert st["source_events"] == {}


# ---- runner integration -------------------------------------------------------------------

class SrcEnv(Env):
    def __init__(self, monkeypatch, web=None):
        super().__init__(monkeypatch, [mkplay("zz")], {"portals": []})
        self.src = web or Web()

    def go(self, now, **kw):
        kw.setdefault("portal_fetch_fn", self.src.fetch)
        return self.run(now, **kw)


def test_run_once_records_source_status_events_and_state(monkeypatch):
    env = SrcEnv(monkeypatch)
    env.go(NOV3)                                   # baseline: nothing announced
    st = env.saved_state()
    rec = st["sources"]["basiskele"]
    assert rec["ok"] is True and rec["items_count"] == 13 and rec["error"] is None
    assert len(st["source_events"]) == 13 and ARTICLE_URL in st["src_cache"]["basiskele"]["articles"]
    assert not any(k.startswith("SRC_") for k in st["emitted"])
    first_change = rec["last_change_at"]
    env.go(NOV3 + timedelta(minutes=5))
    rec = env.saved_state()["sources"]["basiskele"]
    assert rec["last_change_at"] == first_change   # same program, only the check time moved


def test_new_event_in_the_program_is_delivered_once(monkeypatch):
    env = SrcEnv(monkeypatch)
    env.go(NOV3)
    env.tg.sent.clear()
    extra = ART.replace("<p>26 Kasım &Ccedil;arşamba",
                        "<p>27 Kasım Perşembe &ndash; 20.00 &ndash; Yeni Oyun (Yetişkin Tiyatrosu) "
                        "&ndash; Gençlik Merkezi</p>\n\n<p>26 Kasım &Ccedil;arşamba")
    assert extra != ART
    env.src.article = extra
    # wave 6: nothing is followed by default, so the new event is announced only once the
    # user follows it (an unfollowed new event stays silent, see test_wave6)
    env.store.files["config.json"] = ({"plays": {}, "portals": [],
                                       "event_follow": {"bk:20251127-2000-yeni-oyun": True}},
                                      "cfg")
    env.go(NOV3 + timedelta(minutes=5))
    assert len(env.tg.sent) == 1
    msg = env.tg.sent[0]
    assert "Başiskele Belediyesi: 🆕 yeni etkinlik duyuruldu" in msg and "Yeni Oyun" in msg
    assert "27 Kasım Perşembe 20:00" in msg
    env.go(NOV3 + timedelta(minutes=10))
    assert len(env.tg.sent) == 1                   # once per event id
    rec = env.saved_state()["sources"]["basiskele"]
    assert rec["last_change_at"] == (NOV3 + timedelta(minutes=5)).isoformat()


def test_source_failure_never_breaks_the_biletinial_scan(monkeypatch):
    env = SrcEnv(monkeypatch)
    env.src.fail = {S.BASISKELE_RSS}
    res = env.go(NOV3)
    assert res.scanned == 1
    rec = env.saved_state()["sources"]["basiskele"]
    assert rec["ok"] is False and rec["error"] == "FetchError"
    env.src.fail = set()
    env.go(NOV3 + timedelta(minutes=5))
    assert env.saved_state()["sources"]["basiskele"]["ok"] is True


def test_sources_are_not_read_without_an_injected_fetcher_or_in_burst(monkeypatch):
    env = SrcEnv(monkeypatch)
    env.run(NOV3)                                  # no portal_fetch_fn
    assert env.src.calls == [] and "basiskele" not in env.saved_state().get("sources", {})
    env.go(NOV3 + timedelta(minutes=1), only_slugs=["zz"])
    assert env.src.calls == []


def test_paused_config_discards_source_events(monkeypatch):
    env = SrcEnv(monkeypatch)
    env.store.files["config.json"] = ({"paused": True, "portals": []}, "c")
    env.go(NOV3)
    env.tg.sent.clear()
    env.src.article = ART.replace("Cesur", "Cesur Yeni")
    env.go(NOV3 + timedelta(minutes=5))
    assert env.tg.sent == []


def test_fingerprint_ignores_source_check_times():
    st = base_state()
    fold(st, [listing()], NOV3)
    fp = _fingerprint(st)
    fold(st, [listing()], NOV3 + timedelta(minutes=5))
    assert _fingerprint(st) == fp


# ---- notification text --------------------------------------------------------------------

def test_source_notification_text_is_escaped_and_bounded():
    ev = Event("SRC_NEW", "SRC_NEW:x", "", None, [],
               {"source": "basiskele", "title": "A <b>&</b> " + "x" * 600,
                "start": NOV3.isoformat(), "kind": "Çocuk Tiyatrosu", "venue": "Salon <1>",
                "url": "https://example.test/a?b=1&c=2", "opens_at": None})
    [text] = format_message([ev], {}, cfg())
    assert text.startswith("🎭 Başiskele Belediyesi: 🆕 yeni etkinlik duyuruldu")
    assert "<b>A &lt;b&gt;&amp;&lt;/b&gt;" in text and "Salon &lt;1&gt;" in text
    assert "https://example.test/a?b=1&amp;c=2" in text and len(text) <= MAX_LEN


# ---- PC service: activity log + desktop text -------------------------------------------------

def test_pc_service_logs_source_checks_and_notifies(monkeypatch, tmp_path):
    from pc.service import Service
    from pc.viewmodel import headline
    from tests.test_pc_service import NOW as PNOW, make, tick_at
    from tiyatro.claims import ClaimStore
    e = make(monkeypatch, tmp_path)
    e.store.files["config.json"] = ({"portals": []}, "cfg")
    web = Web()
    e.svc = Service(e.store, e.tg, ClaimStore(e.store, "pc", e.clock), clock=e.clock,
                    fetch_fn=e.web.fetch, sleep=lambda s: None, local_dir=tmp_path,
                    rand=lambda a, b: 0.0, portal_fetch_fn=web.fetch)
    notes = []
    e.svc.on_notify(lambda t, x, evs: notes.append((t, x, evs)))
    tick_at(e, PNOW)
    got = list(reversed(e.svc.activity.load()))
    line = [x for x in got if x["title"].startswith("Başiskele Belediyesi:")]
    assert line and line[0]["cat"] == "Liste kontrolü" and "13 etkinlik" in line[0]["title"]
    assert [v["id"] for v in e.svc.sources()] == ["biletinial", "basiskele", "vkv"]
    web.fail = {S.BASISKELE_RSS}
    tick_at(e, PNOW + timedelta(minutes=5))
    warn = [x for x in reversed(e.svc.activity.load())
            if x["cat"] == "Hata/Uyarı" and "Başiskele" in x["title"]]
    assert warn and warn[0]["level"] == "warn"
    assert e.svc.sources()[1]["status"] == "failed"
    ev = Event("SRC_NEW", "k", "", None, [], {"source": "basiskele", "title": "X"})
    assert headline(ev) == "🎭 Başiskele Belediyesi: 🆕 yeni etkinlik duyuruldu"
