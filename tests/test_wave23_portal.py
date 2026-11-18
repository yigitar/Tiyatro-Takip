"""Wave 23: structured Başiskele reservation-portal parser, transition-only alerts, one-line
messages without repeated links, merge with the article events (no network)."""
from __future__ import annotations

import copy
import pathlib
import re
from datetime import datetime, timedelta

import pytest

import tiyatro.runner as runner
from tests.test_runner import NOW, Env, mkplay
from tests.test_wave3c_runner import CFG, PORTAL_URL, PortalWeb, at, go, make, pstate
from tiyatro import srcevents
from tiyatro.config import config_from_dict
from tiyatro.models import ISTANBUL, Event
from tiyatro.notify import dedupe_urls, format_message
from tiyatro.portals import (match_portal_event, norm_title, parse_portal, seat_status,
                             stable_title)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
LIVE = (FIXTURES / "basiskele_portal_events_live_2026-11-18.html").read_text(encoding="utf-8")
BASE = PORTAL_URL
SEATS = re.compile(r"(Rezerve Edilmiş Koltuk Sayısı:\s*<span[^>]*>\s*)(\d+)(\s*</span>)")
P_URL = "https://rezervasyon.basiskele.bel.tr/etkinlik-d/0/1084/puskullu-muamma.aspx"
K_URL = "https://rezervasyon.basiskele.bel.tr/etkinlik-d/0/1083/kediler-sokagi.aspx"


def page(puskullu=245, kediler=590, total=None):
    """The live fixture with the reserved-seat counters replaced (first card, second card)."""
    counts = iter([puskullu, kediler])
    html = SEATS.sub(lambda m: m.group(1) + str(next(counts)) + m.group(3), LIVE)
    if total is not None:
        html = re.sub(r"(Mevcut Koltuk Sayısı:\s*<span[^>]*>\s*)590", rf"\g<1>{total}", html)
    return html


def urls_in(text: str) -> list[str]:
    return re.findall(r"https?://[^\s<>\"]+", text)


def assert_no_repeated_url(text: str) -> None:
    found = urls_in(text)
    assert len(found) == len(set(found)), text


def portal_msgs(env) -> list[str]:
    return [m for m in env.tg.sent if "Başiskele" in m]


# ---- 1. the parser ---------------------------------------------------------------------

def test_live_page_is_read_as_two_structured_events():
    snap = parse_portal(LIVE, BASE)
    assert snap.structured and snap.empty is False and len(snap.events) == 2
    p, k = snap.events
    assert (p["id"], p["title"], p["date"], p["time"], p["venue"]) == (
        "1084", "PÜSKÜLLÜ MUAMMA", "20.11.2026", "20:00", "Türkiye Yüzyılı Gençlik Merkezi")
    assert p["start"] == "2026-11-20T20:00:00+03:00" and p["url"] == P_URL
    assert (p["total"], p["reserved"], p["free"], p["status"]) == (590, 245, 345, "open")
    assert (k["id"], k["title"], k["time"], k["url"]) == ("1083", "KEDİLER SOKAĞI", "15:00", K_URL)
    assert (k["total"], k["reserved"], k["free"], k["status"]) == (590, 590, 0, "full")
    # one line per event for the stored lines / items, one link each
    assert snap.lines[0] == "PÜSKÜLLÜ MUAMMA · 20.11 20:00 · Türkiye Yüzyılı Gençlik Merkezi · boş 345/590"
    assert [i["url"] for i in snap.items] == [P_URL, K_URL]


def test_the_saved_first_observation_also_reads():
    saved = pathlib.Path(__file__).parent.parent / "Working"
    snap = parse_portal(page(240, 173), BASE)
    assert [e["reserved"] for e in snap.events] == [240, 173]
    assert [e["free"] for e in snap.events] == [350, 417]


def test_counters_never_change_the_signature_identity_does():
    a, b = parse_portal(page(240, 173), BASE), parse_portal(page(300, 590), BASE)
    assert a.signature == b.signature
    renamed = parse_portal(page().replace("PÜSKÜLLÜ MUAMMA", "PÜSKÜLLÜ MUAMMA 2"), BASE)
    assert renamed.signature != a.signature


def test_other_markup_with_the_same_words_still_parses():
    html = ("<html><body><ul>"
            "<li><b>Gece Yarısı</b><br>21.11.2026 19:30<br>Salon A<br>"
            "Mevcut Koltuk Sayısı: 100<br>Rezerve Edilmiş Koltuk Sayısı: 40<br>"
            "<a href='/etkinlik-d/0/7/gece.aspx'>Detay</a></li>"
            "<li><b>Sabah</b><br>22.11.2026 10:00<br>Salon B<br>"
            "Mevcut Koltuk Sayısı: 50<br>Rezerve Edilmiş Koltuk Sayısı: 50<br>"
            "<a href='/etkinlik-d/0/8/sabah.aspx'>Detay</a></li></ul></body></html>")
    snap = parse_portal(html, BASE)
    assert snap.structured
    assert [(e["id"], e["title"], e["free"], e["status"]) for e in snap.events] == [
        ("7", "Gece Yarısı", 60, "open"), ("8", "Sabah", 0, "full")]
    assert snap.events[0]["url"] == "https://rezervasyon.basiskele.bel.tr/etkinlik-d/0/7/gece.aspx"


def test_hidden_reservation_button_means_closed_but_full_stays_full():
    closed = LIVE.replace('class="col-md-12 text-center" style="display:block"',
                          'class="col-md-12 text-center" style="display:none"')
    snap = parse_portal(closed, BASE)
    assert [e["status"] for e in snap.events] == ["closed", "full"]
    assert [e["bookable"] for e in snap.events] == [False, False]
    assert seat_status({"total": 10, "free": 0, "bookable": False}) == "full"
    assert seat_status({"bookable": True}) == "unknown"


def test_a_page_without_cards_is_not_structured():
    snap = parse_portal("<html><body><p>Etkinlik bulunamadı</p></body></html>", BASE)
    assert not snap.structured and snap.events == []


def test_title_matching_ignores_case_accents_and_a_programi_suffix():
    assert norm_title("PÜSKÜLLÜ MUAMMA") == norm_title("Püsküllü Muamma") == "puskullu muamma"
    assert norm_title("Kediler Sokağı Programı") == "kediler sokagi"
    state = {"portals": {"basiskele": {"events": {"1": {
        "title": "KEDİLER SOKAĞI", "start": "2026-11-20T15:00:00+03:00"}}}}}
    assert match_portal_event(state, "Kediler Sokağı", "2026-11-20T15:00:00+03:00")
    assert match_portal_event(state, "Kediler Sokağı", "2026-11-21T15:00:00+03:00") is None


def test_counter_titles_are_stable_for_the_generic_watcher():
    assert stable_title("Rezerve Edilmiş Koltuk Sayısı: 173") == stable_title(
        "Rezerve Edilmiş Koltuk Sayısı: 240")
    assert stable_title("Konser 1") != stable_title("Konser 2")


# ---- 2. alert semantics ----------------------------------------------------------------

def scan(env, n, html):
    env.portal.html = html
    return go(env, n)


def kinds_of(res):
    return [e.kind for e in res.events if e.kind.startswith("PORTAL_")]


def test_twenty_scans_with_changing_counters_send_nothing_after_the_discovery(monkeypatch):
    env = make(monkeypatch, html=page(173, 590))
    first = go(env, 0)
    assert kinds_of(first) == ["PORTAL_NEW"]
    sent_after_discovery = len(env.tg.sent)
    assert sent_after_discovery == 1
    for n in range(1, 21):
        res = scan(env, n, page(173 + 7 * n, 590))      # somebody reserves every scan
        assert kinds_of(res) == [], n
    assert len(env.tg.sent) == sent_after_discovery
    assert not any(k.startswith("PORTAL_OPENED") for k in env.saved_state()["emitted"])


def test_full_to_free_alerts_exactly_once_then_once_more_later(monkeypatch):
    env = make(monkeypatch, html=page(245, 590))
    go(env, 0)
    env.tg.sent.clear()
    res = scan(env, 1, page(245, 589))                  # Kediler: 1 seat free
    assert kinds_of(res) == ["PORTAL_SEATS"]
    assert len(env.tg.sent) == 1 and "YER AÇILDI" in env.tg.sent[0]
    for n in range(2, 8):                               # stays free, counters move: silent
        assert kinds_of(scan(env, n, page(245, 589 - n))) == []
    assert len(env.tg.sent) == 1
    assert kinds_of(scan(env, 8, page(245, 590))) == []   # full again: silent
    assert kinds_of(scan(env, 9, page(245, 590))) == []
    res = scan(env, 10, page(245, 580))                 # free again, minutes later
    assert kinds_of(res) == ["PORTAL_SEATS"]
    assert len(env.tg.sent) == 2
    keys = [k for k in env.saved_state()["emitted"] if k.startswith("PORTAL_SEATS:")]
    assert sorted(keys) == ["PORTAL_SEATS:basiskele:1083:1", "PORTAL_SEATS:basiskele:1083:2"]


def test_a_flap_inside_the_gap_is_one_alert(monkeypatch):
    env = make(monkeypatch, html=page(245, 590))
    go(env, 0)
    env.tg.sent.clear()
    t = at(1)
    for i, k in enumerate((589, 590, 588)):             # free, full, free within 3 minutes
        env.portal.html = page(245, k)
        env.run(t + timedelta(seconds=40 * i), portal_fetch_fn=env.portal.fetch)
    assert len(env.tg.sent) == 1


def test_a_new_portal_event_is_announced_once_with_one_line_per_event(monkeypatch):
    env = make(monkeypatch, html=page())
    res = go(env, 0)
    assert kinds_of(res) == ["PORTAL_NEW"] and len(env.tg.sent) == 1
    msg = env.tg.sent[0]
    assert msg.startswith("🏛️ Başiskele · 2 yeni etkinlik")
    assert msg.count("PÜSKÜLLÜ MUAMMA") == 1 and msg.count("KEDİLER SOKAĞI") == 1
    assert "boş 345/590" in msg and "DOLU 590/590" in msg
    assert_no_repeated_url(msg)
    assert msg.count(P_URL) == 1 and msg.count(K_URL) == 1
    assert msg.rstrip().endswith(PORTAL_URL) and msg.count(PORTAL_URL) == 1
    for n in range(1, 4):                               # rescans never repeat it
        assert kinds_of(scan(env, n, page())) == []
    assert len(env.tg.sent) == 1


def test_events_known_from_articles_are_merged_and_silent(monkeypatch):
    env = make(monkeypatch, html=page())
    go(env, 0)                                          # baseline of the plays
    st = env.saved_state()
    st["portals"] = {}
    st["emitted"] = {k: v for k, v in st["emitted"].items() if not k.startswith("PORTAL_")}
    st["source_events"] = {
        "bk:20261120-1500-kediler-sokagi": {
            "id": "bk:20261120-1500-kediler-sokagi", "source": "basiskele",
            "title": "Kediler Sokağı", "start": "2026-11-20T15:00:00+03:00",
            "venue": "Türkiye Yüzyılı Gençlik Merkezi", "url": "https://www.basiskele.bel.tr/x",
            "full": None, "opens_at": None, "opens_pending": None, "follow_default": False},
        "bk:20261120-2000-puskullu-muamma": {
            "id": "bk:20261120-2000-puskullu-muamma", "source": "basiskele",
            "title": "Püsküllü Muamma", "start": "2026-11-20T20:00:00+03:00",
            "venue": "Türkiye Yüzyılı Gençlik Merkezi", "url": "https://www.basiskele.bel.tr/y",
            "full": None, "opens_at": None, "opens_pending": None, "follow_default": False}}
    env.store.files["state.json"] = (st, "x")
    env.tg.sent.clear()
    res = scan(env, 1, page())
    assert kinds_of(res) == [] and env.tg.sent == []
    saved = env.saved_state()
    assert set(saved["source_events"]) == {"bk:20261120-1500-kediler-sokagi",
                                           "bk:20261120-2000-puskullu-muamma"}
    assert set(saved["portals"]["basiskele"]["events"]) == {"1083", "1084"}
    # the article event now carries the live seat facts
    cfg = config_from_dict({})
    rows = {r["id"]: r for r in srcevents.event_views(saved, cfg, NOW)}
    k = rows["bk:20261120-1500-kediler-sokagi"]
    assert k["portal"]["status"] == "full" and k["portal"]["url"] == K_URL
    p = rows["bk:20261120-2000-puskullu-muamma"]
    assert (p["portal"]["free"], p["portal"]["total"]) == (345, 590)


def test_a_portal_only_event_gets_a_followable_record_and_the_article_later_merges(monkeypatch):
    env = make(monkeypatch, html=page())
    go(env, 0)
    st = env.saved_state()
    rec = st["source_events"]["bkp1084"]
    assert rec["title"] == "Püsküllü Muamma" and rec["follow_default"] is False
    assert rec["url"] == P_URL and rec["portal_eid"] == "1084"
    # the article about it appears later: one record, no second discovery notice
    ls = srcevents.Listing(source="basiskele", id="bk:20261120-2000-puskullu-muamma",
                           title="Püsküllü Muamma", start=datetime(2026, 11, 20, 20, 0, tzinfo=ISTANBUL),
                           venue="Türkiye Yüzyılı Gençlik Merkezi", url="https://www.basiskele.bel.tr/y")
    events = srcevents.fold_listings(st, config_from_dict({}), "basiskele", [ls], at(2))
    assert [e.kind for e in events] == []
    assert "bkp1084" not in st["source_events"]
    assert "bk:20261120-2000-puskullu-muamma" in st["source_events"]
    assert not any(k.startswith("SRC_NEW:bk:20261120-2000") for k in st["emitted"])


def test_removed_events_leave_silently_and_come_back_without_a_new_notice(monkeypatch):
    env = make(monkeypatch, html=page())
    go(env, 0)
    env.tg.sent.clear()
    from tests.test_wave3c_portals import real_empty
    gone = real_empty()
    assert kinds_of(scan(env, 1, gone)) == [] and env.tg.sent == []   # empty list: silent
    assert pstate(env)["empty"] is True
    assert all(not e["present"] for e in pstate(env)["events"].values())
    assert kinds_of(scan(env, 2, page())) == [] and env.tg.sent == []
    assert all(e["present"] for e in pstate(env)["events"].values())


def test_not_bookable_to_open_alerts(monkeypatch):
    closed = LIVE.replace('class="col-md-12 text-center" style="display:block"',
                          'class="col-md-12 text-center" style="display:none"')
    env = make(monkeypatch, html=closed)
    go(env, 0)
    env.tg.sent.clear()
    assert kinds_of(scan(env, 1, closed)) == []
    res = scan(env, 2, LIVE)                            # button visible: bookable now
    assert kinds_of(res) == ["PORTAL_SEATS"]
    assert "rezervasyon açıldı" in env.tg.sent[0] and "boş 345/590" in env.tg.sent[0]


def test_seat_alert_uses_the_urgent_path_and_one_claim_per_event(monkeypatch):
    from tiyatro.claims import claim_key
    assert claim_key("PORTAL_SEATS:basiskele:1083:3") == "PORTAL_SEATS:basiskele:1083"
    assert claim_key("PORTAL_SEATS:basiskele:1083:3") == claim_key("PORTAL_SEATS:basiskele:1083:4")
    assert runner.pc_delay("PORTAL_SEATS:basiskele:1083:1") == runner.PC_ON_SALE_DELAY
    assert runner.pc_delay("PORTAL_NEW:basiskele:abc") == runner.PC_DELAY


# ---- 3. markup drift -------------------------------------------------------------------

def test_markup_drift_after_structured_reads_gives_one_notice_per_day(monkeypatch):
    env = make(monkeypatch, html=page())
    go(env, 0)
    env.tg.sent.clear()
    drift = ("<html><body><div><h2>Yeni tasarım</h2><p>PÜSKÜLLÜ MUAMMA 20 Kasım saat 20:00 "
             "bilet için başvurun, yer sayısı sınırlıdır.</p><p>KEDİLER SOKAĞI 20 Kasım "
             "saat 15:00 bilgi için arayın.</p></div></body></html>")
    msgs = []
    for n in range(1, 30):
        res = scan(env, n, drift)
        msgs += [e.kind for e in res.events if e.kind.startswith("PORTAL_")]
    assert msgs == ["PORTAL_MARKUP"]
    assert len(env.tg.sent) == 1 and "düzeni değişmiş olabilir" in env.tg.sent[0]
    assert "PÜSKÜLLÜ" not in env.tg.sent[0]
    assert set(pstate(env)["events"]) == {"1083", "1084"}   # last known events are kept


def test_generic_watcher_still_works_when_the_portal_never_had_cards(monkeypatch):
    env = make(monkeypatch, html="<html><body></body></html>")
    go(env, 0)
    assert pstate(env).get("mode") != "structured"
    res = env.run(at(1), portal_fetch_fn=env.portal.fetch)
    assert kinds_of(res) == []


# ---- 4. messages -------------------------------------------------------------------------

def mk_event(kind, payload):
    return Event(kind, f"{kind}:t", "", None, [], payload)


def all_builder_messages():
    ev = {"id": "1084", "title": "PÜSKÜLLÜ MUAMMA", "date": "20.11.2026", "time": "20:00",
          "venue": "Türkiye Yüzyılı Gençlik Merkezi", "total": 590, "reserved": 240, "free": 350,
          "status": "open", "url": P_URL, "followed": False}
    base = {"portal_id": "basiskele", "title": "Başiskele Belediyesi rezervasyon", "url": PORTAL_URL}
    article = {"source": "basiskele", "event_id": "bk:x", "title": "Püsküllü Muamma",
               "kind": "Tiyatro", "start": "2026-11-20T20:00:00+03:00", "venue": "TYGM",
               "url": "https://www.basiskele.bel.tr/haber/x", "opens_at": None}
    cfg = config_from_dict({})
    cases = [
        mk_event("PORTAL_NEW", {**base, "events": [ev, {**ev, "id": "1083", "url": K_URL}],
                                "event_count": 2}),
        mk_event("PORTAL_NEW", {**base, "events": [ev], "event_count": 1}),
        mk_event("PORTAL_SEATS", {**base, "event": ev, "reason": "freed", "followed": True}),
        mk_event("PORTAL_SEATS", {**base, "event": ev, "reason": "opened", "followed": False}),
        mk_event("PORTAL_MARKUP", {**base, "detail": "değişmiş olabilir"}),
        mk_event("PORTAL_FAILING", {**base, "detail": "3 taramadır okunamıyor"}),
        # the OLD generic notice with the real flattened item list
        mk_event("PORTAL_OPENED", {**base, "item_count": 16, "lines": [], "items": [
            {"title": "PÜSKÜLLÜ MUAMMA", "url": P_URL}, {"title": "Etkinlik", "url": P_URL},
            {"title": "Türkiye Yüzyılı Gençlik Merkezi", "url": P_URL},
            {"title": "Mevcut Koltuk Sayısı: 590", "url": P_URL},
            {"title": "Rezerve Edilmiş Koltuk Sayısı: 173", "url": P_URL},
            {"title": "Etkinlik", "url": PORTAL_URL}]}),
        mk_event("SRC_NEW", {**article, "followed": False, "portal_url": P_URL}),
        mk_event("SRC_OPENED", {**article, "portal_url": P_URL}),
        mk_event("SRC_REOPENED", {**article}),
        mk_event("SRC_DAY_BEFORE", {**article, "url": PORTAL_URL}),
    ]
    out = []
    for c in cases:
        out.extend(format_message([c], {}, cfg))
    out.extend(format_message(cases[:2] + cases[7:9], {}, cfg))
    return out


def test_no_url_appears_twice_in_any_message_of_any_builder():
    msgs = all_builder_messages()
    assert len(msgs) >= 10
    for m in msgs:
        assert_no_repeated_url(m)


def test_the_old_generic_notice_is_one_bullet_per_link_not_per_line():
    msgs = format_message([mk_event("PORTAL_OPENED", {
        "portal_id": "basiskele", "title": "x", "url": PORTAL_URL, "item_count": 16, "lines": [],
        "items": [{"title": t, "url": P_URL} for t in (
            "PÜSKÜLLÜ MUAMMA", "Etkinlik", "Türkiye Yüzyılı Gençlik Merkezi",
            "Mevcut Koltuk Sayısı: 590", "Rezerve Edilmiş Koltuk Sayısı: 173")]}),
    ], {}, config_from_dict({}))
    assert msgs[0].count(P_URL) == 1 and msgs[0].count("•") == 1


def test_event_page_link_replaces_the_portal_root_in_article_notices():
    cfg = config_from_dict({})
    art = {"source": "basiskele", "event_id": "bk:x", "title": "Püsküllü Muamma",
           "start": "2026-11-20T20:00:00+03:00", "url": "https://www.basiskele.bel.tr/h",
           "portal_url": P_URL}
    msg = format_message([mk_event("SRC_OPENED", art)], {}, cfg)[0]
    assert f"Rezervasyon portalı: {P_URL}" in msg and PORTAL_URL + "\n" not in msg + "\n"


def test_dedupe_urls_keeps_text_and_drops_empty_label_lines():
    text = "A · http://x/1\n• T — http://x/1\n🔗 http://x/1\n🏛️ Rezervasyon portalı: http://x/1\nz http://y"
    assert dedupe_urls(text).split("\n") == ["A · http://x/1", "• T", "z http://y"]
