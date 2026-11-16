"""Wave 17: Başiskele venue / performer extraction from the narrative program article."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tiyatro import srcevents as S
from tiyatro.models import ISTANBUL

FIX = Path(__file__).parent / "fixtures"
PUB = datetime(2026, 10, 30, 14, 0, tzinfo=ISTANBUL)
TYGM = "Türkiye Yüzyılı Gençlik Merkezi"


def parse(html: str):
    return {e.title: e for e in S.parse_article(html, "u", PUB)[0]}


def para(*sentences: str) -> str:
    return ('<span id="ContentPlaceHolder1_lbaciklama"><p>' + " ".join(sentences) + "</p></span>")


def test_real_november_2026_article_venues():
    ev = parse((FIX / "bk_art_2026_11.html").read_text(encoding="utf-8"))
    assert ev["Kitaplar Ülkesi"].venue == TYGM and not ev["Kitaplar Ülkesi"].venue_derived
    # "yine aynı merkezde" right after a sentence that states TYGM: resolved, marked derived
    assert ev["Rapunzel"].venue == TYGM and ev["Rapunzel"].venue_derived
    # no venue in the sentence and no "aynı ..." wording: nothing is assumed
    assert ev["Unutmak"].venue == "" and not ev["Unutmak"].venue_derived
    assert ev["Satır Arası Ebeveynlik"].venue == "Kitaphane"
    # one venue stated after two coordinated titles of one sentence: both
    assert ev["Kediler Sokağı"].venue == TYGM and ev["Taş Prenses"].venue == TYGM
    assert not ev["Kediler Sokağı"].venue_derived and not ev["Taş Prenses"].venue_derived


def test_same_place_needs_a_venue_in_the_preceding_sentence():
    html = para("1 Kasım Pazar günü saat 16.00’da “Aa” adlı çocuk tiyatrosu sahnelenecek.",
                "6 Kasım Cuma günü saat 19.30’da yine aynı merkezde “Bb” oyunu oynanacak.")
    ev = parse(html)
    assert ev["Bb"].venue == "" and not ev["Bb"].venue_derived


def test_same_place_with_two_venues_in_preceding_sentence_is_not_resolved():
    html = para("1 Kasım Pazar günü saat 16.00’da Kitaphane’de “Aa” ve Kültür Merkezi’nde “Cc” başlayacak.",
                "6 Kasım Cuma günü saat 19.30’da aynı salonda “Bb” oyunu oynanacak.")
    assert parse(html)["Bb"].venue == ""


def test_company_stated_in_the_sentence_becomes_sahneleyen():
    html = para("Kocaeli Şehir Tiyatroları tarafından 4 Kasım Salı günü saat 14.00’te "
                "Gençlik Merkezi’nde sahnelenecek “Çöp Atlas” adlı çocuk tiyatrosu.",
                "7 Kasım Cuma günü saat 19.30’da “Başka” adlı tiyatro oyunu oynanacak.")
    ev = parse(html)
    assert ev["Çöp Atlas"].info["credits"] == {"Sahneleyen": "Kocaeli Şehir Tiyatroları"}
    assert ev["Çöp Atlas"].venue == "Gençlik Merkezi"
    assert "credits" not in ev["Başka"].info          # another sentence: nothing carried over


def test_organiser_is_not_a_company_and_district_is_not_a_venue():
    html = para("Başiskele Belediyesi tarafından düzenlenen 4 Kasım Salı günü saat 14.00’te "
                "Başiskele’de sahnelenecek “Xx” adlı çocuk tiyatrosu.")
    e = parse(html)["Xx"]
    assert e.venue == "" and "credits" not in e.info


def test_table_lines_keep_their_own_venue():
    ev = {e.title: e for e in S.parse_article(
        (FIX / "bk_art.html").read_bytes().decode("iso-8859-9"), "u",
        datetime(2025, 10, 31, tzinfo=ISTANBUL))[0]}
    assert ev["Cesur İtfaiyeci"].venue == "Türkiye Yüzyılı Gençlik Merkezi"
    assert not ev["Cesur İtfaiyeci"].venue_derived


def test_record_stores_venue_and_derived_flag_and_notice_hides_derived_venue():
    from tests.test_wave4c_basiskele import cfg
    state: dict = {"emitted": {}}
    ls = [S.Listing(S.BASISKELE, "bk:1", "A", "", datetime(2026, 11, 20, 20, tzinfo=ISTANBUL), TYGM,
                    "u", venue_derived=True, detail_ok=True, info={})]
    S.fold_listings(state, cfg(), S.BASISKELE, ls, datetime(2026, 11, 16, tzinfo=ISTANBUL))
    rec = state["source_events"]["bk:1"]
    assert rec["venue"] == TYGM and rec["venue_derived"] is True
    assert S._payload(rec)["venue"] == ""            # a notice never states a derived venue as fact
    rec["venue_derived"] = False
    assert S._payload(rec)["venue"] == TYGM
    view = S.event_views(state, cfg(), datetime(2026, 11, 16, tzinfo=ISTANBUL))[0]
    assert view["venue"] == TYGM and view["venue_derived"] is False
