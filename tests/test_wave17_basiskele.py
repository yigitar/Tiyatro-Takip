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


# ---- wave 18: sentences the parser used to drop ---------------------------------------------

def test_wave18_real_article_live_fixture_parses_every_theatre_and_series_event():
    html = (FIX / "bk_art_12879_live_2026-11-16.html").read_text(encoding="utf-8")
    assert S.parse_article(html, "u", PUB)[0] == S.parse_article(
        (FIX / "bk_art_2026_11.html").read_text(encoding="utf-8"), "u", PUB)[0]
    ev = parse(html)
    pm = ev["Püsküllü Muamma"]
    assert (pm.start.day, pm.start.hour, pm.start.minute) == (20, 20, 0)
    assert pm.venue == TYGM and not pm.venue_derived       # venue stated in its own sentence
    ks = ev["Kitaphane Kültür Söyleşileri"]
    assert (ks.start.day, ks.start.hour) == (25, 20)
    # the unchanged ones
    assert ev["Satır Arası Ebeveynlik"].start.day == 20 and ev["Kediler Sokağı"].start.hour == 15
    # wave 20: "<Name> programı" after the time and venue is an event (title without "programı")
    assert ev["Evlilik Okulu"].start.day == 22 and ev["Evlilik Okulu"].start.hour == 14
    assert len(ev) == 9


def test_same_day_needs_exactly_one_explicit_date_in_the_previous_sentence():
    one = para("20 Kasım Cuma günü saat 11.00’de Kitaphane’de “Aa” programı olacak.",
               "Aynı gün saat 20.00’de Merkez Salonu’nda “Bb” oyunu oynanacak.")
    ev = parse(one)
    assert ev["Bb"].start.day == 20 and ev["Bb"].start.hour == 20
    two = para("20 Kasım Cuma günü saat 15.00’te “Aa”, 22 Kasım Pazar günü saat 16.00’da ise “Cc” "
               "adlı çocuk tiyatroları sahnelenecek.",
               "Aynı gün saat 20.00’de “Bb” oyunu oynanacak.")
    assert "Bb" not in parse(two)                  # two dates before: ambiguous, nothing guessed
    first = para("Aynı gün saat 20.00’de “Bb” oyunu oynanacak.")
    assert parse(first) == {}
    gap = para("20 Kasım Cuma günü saat 15.00’te “Aa” oyunu olacak.", "Başka bir cümle.",
               "Aynı gün saat 20.00’de “Bb” oyunu oynanacak.")
    assert "Bb" not in parse(gap)                  # only the immediately preceding sentence


def test_series_title_only_directly_after_the_time_and_before_kapsaminda():
    ok = para("25 Kasım Çarşamba günü saat 20.00’de ise Kitaphane Kültür Söyleşileri kapsamında "
              "kitapseverler bir araya gelecek.")
    assert list(parse(ok)) == ["Kitaphane Kültür Söyleşileri"]
    far = para("25 Kasım Çarşamba günü saat 20.00’de herkes gelecek, ayrıca Kültür Söyleşileri "
               "kapsamında ikram olacak.")
    assert parse(far) == {}
    lower = para("25 Kasım Çarşamba günü saat 20.00’de söyleşi kapsamında bir araya gelinecek.")
    assert parse(lower) == {}


def test_newly_parsed_events_of_a_known_article_send_one_digest_not_silent():
    from datetime import timedelta
    from tiyatro import runner
    from tiyatro.config import default_config
    from tiyatro.notify import format_message
    from tiyatro.state import empty_state
    html_old = para("1 Kasım Pazar günü saat 16.00’da “Aa” adlı çocuk tiyatrosu olacak. "
                    "20 Kasım Cuma günü saat 11.00’de Kitaphane’de “Cc” programı olacak. "
                    "Aynı gün saat 20.00’de Merkez’de “Bb” oyunu olacak. "
                    "25 Kasım Çarşamba günü saat 20.00’de ise Kitaphane Söyleşileri kapsamında gelinecek.")
    html_new = html_old
    base = [l for l in S.parse_article(html_old, "u", PUB)[0] if l.title in ("Aa", "Cc")]
    now = datetime(2026, 11, 2, 12, 0, tzinfo=ISTANBUL)
    st, cfg = empty_state(), default_config()
    st.setdefault("source_events", {})
    assert S.fold_listings(st, cfg, "basiskele", base, now) == []           # silent baseline
    allev = S.parse_article(html_new, "u", PUB)[0]
    found = S.fold_listings(st, cfg, "basiskele", allev, now + timedelta(minutes=5))
    assert sorted(e.payload["title"] for e in found) == ["Bb", "Kitaphane Söyleşileri"]
    st.setdefault("outbox", [])
    st.setdefault("emitted", {})
    runner._queue_events(st, found, False, now, "github")
    assert len(st["outbox"]) == 1 and st["outbox"][0]["key"].startswith("NEWFOLD:")
    [text] = format_message(runner._entry_events(st["outbox"][0]), {}, cfg)
    assert "(2)" in text and "Bb" in text
    # a single new event stays an ordinary notice
    st2 = {"outbox": [], "emitted": {}}
    runner._queue_events(st2, found[:1], False, now, "github")
    assert not st2["outbox"][0]["key"].startswith("NEWFOLD:")


# ---- wave 20: "<Name> programı" ----------------------------------------------------------------

def test_wave20_name_programi_with_date_time_and_venue():
    ev = parse(para("22 Kasım Pazar günü saat 14.00’te Başiskele Belediyesi Antik Köfte Kafe & "
                    "Restoran’da Evlilik Okulu programı düzenlenecek."))
    e = ev["Evlilik Okulu"]
    assert (e.start.month, e.start.day, e.start.hour) == (11, 22, 14)
    assert e.venue == "Başiskele Belediyesi Antik Köfte Kafe & Restoran"
    no_venue = parse(para("22 Kasım Pazar günü saat 14.00’te Evlilik Okulu programı olacak."))
    assert "Evlilik Okulu" in no_venue
    same_day = parse(para("20 Kasım Cuma günü saat 11.00’de Kitaphane’de “Aa” programı olacak.",
                          "Aynı gün saat 20.00’de Merkez Salonu’nda Aile Buluşması programı yapılacak."))
    assert same_day["Aile Buluşması"].start.day == 20 and same_day["Aile Buluşması"].start.hour == 20


def test_wave20_name_programi_negative_cases():
    # the article header sentence (generic, followed by kapsamında) and a date-less / time-less line
    assert parse(para("22 Kasım Pazar günü saat 14.00’te Kültür Sanat Başiskele programı "
                      "kapsamında etkinlik olacak.")) == {}
    assert parse(para("Evlilik Okulu programı düzenlenecek.")) == {}
    assert parse(para("22 Kasım Pazar günü Antik Kafe’de Evlilik Okulu programı düzenlenecek.")) == {}
    # lowercase name, or more than 4 words, or no locative before the name
    assert parse(para("22 Kasım Pazar günü saat 14.00’te kafe’de evlilik okulu programı olacak.")) == {}
    assert parse(para("22 Kasım Pazar günü saat 14.00’te Ali Veli Can Cem Cin Okulu programı olacak.")) == {}
    # the name must be in the same sentence as the time
    assert parse(para("22 Kasım Pazar günü saat 14.00’te kafe olacak. Evlilik Okulu programı var.")) == {}
