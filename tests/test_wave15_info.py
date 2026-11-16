"""Wave 15 C: Özet / Oyuncular / Yapım (+ Süre / Yaş / Ücret) of Başiskele and VKV events,
exactly as the source states them (never invented)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.test_wave4d_vkv import D540, D541, T0, URL540, URL541, Web, cfg
from tiyatro import srcevents as S
from tiyatro import srcinfo as I
from tiyatro.models import ISTANBUL
from tiyatro.state import empty_state
from tiyatro.tags import clean_summary

FIX = Path(__file__).parent / "fixtures"
BK_ART = (FIX / "bk_art.html").read_bytes().decode("iso-8859-9")


# ---- VKV pages ---------------------------------------------------------------------------

def test_vkv_summary_is_the_description_without_reservation_sentences():
    info540 = S.parse_vkv_detail(D540, None).info
    assert info540["summary"].startswith("İlker Gümüşoluk, tek kişilik gösterisiyle")
    assert info540["summary"].endswith("YouTube için özel projeler üretiyor.")
    assert "\n\n" in info540["summary"]                       # two paragraphs kept apart
    low = info540["summary"].lower()
    assert "rezervasyon" not in low and "dolmuştur" not in low and "dolmuş" not in low
    info541 = S.parse_vkv_detail(D541, None).info
    assert info541["summary"].startswith("“Ceviz Adam Müzikali” eşsiz dansları")
    assert "19 Kasım" not in info541["summary"] and "açılacaktır" not in info541["summary"]
    # nothing the page does not state: no cast / credits / facts on the real pages
    assert set(info540) == {"summary"} and set(info541) == {"summary"}


def test_vkv_no_false_facts_from_prose_like_7den_77ye_or_1000den_fazla():
    html = D541.replace("Ceviz Adam Müzikali”", "Ceviz Adam, 7’den 77’ye herkes, 1000’den fazla gösteri”", 1)
    assert set(S.parse_vkv_detail(html, None).info) == {"summary"}


def test_unlocatable_page_gives_none_so_stored_info_is_kept():
    assert S.parse_vkv_detail("<html><body><p>Bakım</p></body></html>", None).info is None
    assert S.vkv_info("") is None


def test_labelled_lines_become_cast_credits_and_facts():
    lines = ["Komik bir hikaye.", "Oyuncular: AYŞE YILMAZ, Mehmet Kaya ve Ali Veli",
             "Yönetmen: CAN ÖZKAN", "Yazan: Zeynep Demir", "Süre: 90 dk", "Yaş sınırı: 7+",
             "Bilet: 250 TL", "Bilinmeyen: şey", "Rezervasyon 9 Kasım'da açılacaktır."]
    info = I.info_from_lines(lines)
    assert info["cast"] == ["Ayşe Yılmaz", "Mehmet Kaya", "Ali Veli"]
    assert info["credits"] == {"Yönetmen": "Can Özkan", "Yazan": "Zeynep Demir"}
    assert info["duration_min"] == 90 and info["age_limit"] == 7 and info["price"] == "250 TL"
    assert "Bilinmeyen: şey" in info["summary"] and "Komik bir hikaye." in info["summary"]
    assert "Rezervasyon" not in info["summary"] and "Oyuncular" not in info["summary"]


def test_facts_only_when_stated():
    assert I.facts_of("Gösteri 75 dakika sürer. 12+ yaş. Ücretsiz.") == {
        "duration_min": 75, "age_limit": 12, "price": "Ücretsiz"}
    assert I.facts_of("7’den 77’ye herkes, 1000’den fazla gösteri, 15 Yıldır") == {}
    assert I.facts_of("Bilet 1.250 TL") == {"price": "1.250 TL"}


def test_reservation_sentence_inside_a_mixed_paragraph_is_cut_sentence_wise():
    info = I.info_from_lines(["Güzel bir oyun. Rezervasyon 3 Aralık günü saat 12:00'da açılacaktır. "
                              "Herkesi bekleriz."])
    assert info["summary"] == "Güzel bir oyun. Herkesi bekleriz."


# ---- Başiskele ---------------------------------------------------------------------------

def test_basiskele_article_events_state_no_summary_cast_credits():
    """The Nov 2025 fixture lists table lines / short sentences only: nothing to quote."""
    events, _o, _p = S.parse_article(BK_ART, "https://x/a", datetime(2025, 10, 30, tzinfo=ISTANBUL))
    assert events and all(e.info == {} for e in events)


def test_basiskele_stated_facts_of_an_events_own_line_only():
    html = ('<span id="ContentPlaceHolder1_lbaciklama"><p>12 Kasım Çarşamba – 19.00 – Cesur '
            'İtfaiyeci (Animasyon Filmi, 80 dk, 7+ yaş) - Türkiye Yüzyılı Gençlik Merkezi</p>'
            '<p>13 Kasım Perşembe – 10.00 – Atölye – Merkez</p>'
            '<p>Etkinliklerin bir bölümü ücretsiz olacaktır.</p></span>')
    events, _o, _p = S.parse_article(html, "https://x/a", datetime(2026, 10, 30, tzinfo=ISTANBUL))
    by = {e.id.split("-", 2)[-1]: e for e in events}
    assert len(events) == 2, [e.id for e in events]
    cesur = next(e for e in events if "itfaiyeci" in e.id)
    atolye = next(e for e in events if "atolye" in e.id)
    assert cesur.info == {"duration_min": 80, "age_limit": 7}
    assert atolye.info == {}                          # the generic "ücretsiz" line is not per event
    assert by


# ---- folding / cache ---------------------------------------------------------------------

EARLY = datetime(2026, 11, 10, 10, 0, tzinfo=ISTANBUL)     # both fixture events still upcoming


def _scan(web=None, now=EARLY):
    web = web or Web()
    return web, S.scan_vkv(web.fetch, now)


def test_scan_uses_the_same_detail_fetch_no_extra_requests():
    web, scan = _scan()
    assert web.calls == [S.VKV_LIST_URL, URL540, URL541]       # info costs no extra fetch
    by = {l.id: l for l in scan.listings}
    assert by["vkv:541"].info["summary"].startswith("“Ceviz Adam")


def test_fold_stores_info_and_rewrites_only_when_the_content_changed():
    state = empty_state()
    _, scan = _scan()
    S.fold_listings(state, cfg(), "vkv", scan.listings, EARLY)
    rec = state["source_events"]["vkv:541"]
    assert rec["info"]["summary"].startswith("“Ceviz Adam") and rec["info_hash"]
    first = (json.dumps(rec["info"], sort_keys=True), rec["info_hash"])
    _, scan2 = _scan()
    S.fold_listings(state, cfg(), "vkv", scan2.listings, EARLY + timedelta(minutes=5))
    assert (json.dumps(state["source_events"]["vkv:541"]["info"], sort_keys=True),
            state["source_events"]["vkv:541"]["info_hash"]) == first
    changed = D541.replace("eşsiz dansları", "harika dansları")
    _, scan3 = _scan(Web(d541=changed))
    S.fold_listings(state, cfg(), "vkv", scan3.listings, EARLY + timedelta(minutes=10))
    rec3 = state["source_events"]["vkv:541"]
    assert "harika dansları" in rec3["info"]["summary"] and rec3["info_hash"] != first[1]


def test_unreadable_detail_keeps_the_stored_info():
    state = empty_state()
    _, scan = _scan()
    S.fold_listings(state, cfg(), "vkv", scan.listings, EARLY)
    before = state["source_events"]["vkv:541"]["info"]
    web = Web(d541="<html><body>Bakım</body></html>")
    _, scan2 = _scan(web)
    S.fold_listings(state, cfg(), "vkv", scan2.listings, EARLY + timedelta(minutes=5))
    assert state["source_events"]["vkv:541"]["info"] == before
    web3 = Web()
    web3.fail.add(URL541)                                           # fetch error: also kept
    _, scan3 = _scan(web3)
    S.fold_listings(state, cfg(), "vkv", scan3.listings, EARLY + timedelta(minutes=9))
    assert state["source_events"]["vkv:541"]["info"] == before


def test_event_views_and_rows_carry_info():
    from pc import app_logic as L
    from pc.viewmodel import event_row
    state = empty_state()
    _, scan = _scan()
    S.fold_listings(state, cfg(), "vkv", scan.listings, EARLY)
    state["source_events"]["vkv:541"]["info"].update(
        cast=["A B", "C D"], credits={"Yönetmen": "X Y"}, duration_min=60, age_limit=4,
        price="Ücretsiz")
    views = {v["id"]: v for v in S.event_views(state, cfg(), EARLY)}
    row = event_row(views["vkv:541"], EARLY)
    assert row.summary == clean_summary(state["source_events"]["vkv:541"]["info"]["summary"])
    assert row.cast == ["A B", "C D"] and row.credits == [("Yönetmen", "X Y")]
    assert (row.duration, row.age, row.price) == ("60 dk", "4+", "Ücretsiz")
    texts = [c.text for c in L.meta_parts(row)]
    assert "Süre: 60 dk" in texts and "Yaş: 4+" in texts and "Ücret: Ücretsiz" in texts
    empty = event_row(views["vkv:540"], EARLY)                         # 540 states a summary only
    assert empty.cast == [] and empty.credits == []
    assert not any(c.text.startswith(("Süre", "Yaş", "Ücret")) for c in L.meta_parts(empty))


# ---- the PC detail pane ------------------------------------------------------------------

tk = pytest.importorskip("tkinter")
from tests.test_pc_app_smoke import Box, FakeService, tk_root, ui  # noqa: E402,F401
from pc import app_logic as L  # noqa: E402
from pc.ui import SOURCE_NO_INFO  # noqa: E402
from pc.viewmodel import with_events  # noqa: E402


def _events_in_ui(ui, with_cast: bool):
    state = empty_state()
    _, scan = _scan()
    S.fold_listings(state, cfg(), "vkv", scan.listings, datetime.now(ISTANBUL) - timedelta(days=20))
    rec = state["source_events"]["vkv:541"]
    rec["start"] = (datetime.now(ISTANBUL) + timedelta(days=5)).isoformat()
    state["source_events"]["vkv:540"]["start"] = (datetime.now(ISTANBUL) + timedelta(days=6)).isoformat()
    state["source_events"]["vkv:540"]["info"] = {}                    # read, nothing stated
    if with_cast:
        rec["info"].update(cast=["Ayşe Yılmaz", "Ali Veli"],
                           credits={"Yönetmen": "Can Özkan", "Yazan": "Zeynep Demir"})
    base = ui.svc.rows
    ui.views = S.event_views(state, cfg(), datetime.now(ISTANBUL))
    ui.svc.rows = lambda now=None: with_events(base(), ui.views, datetime.now(ISTANBUL))
    ui.refresh_rows()


def test_event_detail_shows_ozet_oyuncular_yapim_as_normal_text(ui):
    _events_in_ui(ui, with_cast=True)
    ui.select_slug("vkv:541")
    labels = ui.event_info_labels
    assert labels["Özet"].cget("text").startswith("“Ceviz Adam Müzikali” eşsiz dansları")
    assert labels["Oyuncular"].cget("text") == "Ayşe Yılmaz, Ali Veli"
    assert "Yönetmen: Can Özkan" in labels["Yapım"].cget("text")
    for lbl in labels.values():
        assert str(lbl.cget("foreground")) != L.GRAY             # source-quoted: normal colour
        assert "italic" not in str(lbl.cget("font")).lower()


def test_event_detail_says_kaynakta_bilgi_yok_in_grey_when_the_source_gave_nothing(ui):
    _events_in_ui(ui, with_cast=False)
    ui.select_slug("vkv:541")                       # a summary, but no cast / credits on the page
    labels = ui.event_info_labels
    assert labels["Özet"].cget("text").startswith("“Ceviz Adam")
    for name in ("Oyuncular", "Yapım"):
        assert labels[name].cget("text") == SOURCE_NO_INFO == "Kaynakta bilgi yok"
        assert str(labels[name].cget("foreground")) == L.GRAY
    ui.select_slug("vkv:540")
    ui.root.update()
    assert ui.event_info_labels["Özet"].cget("text") == SOURCE_NO_INFO
    assert str(ui.event_info_labels["Özet"].cget("foreground")) == L.GRAY
