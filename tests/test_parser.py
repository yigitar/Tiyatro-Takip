from datetime import datetime
from pathlib import Path

import pytest

from tiyatro.models import ISTANBUL, Status
from tiyatro.parser import ParseError, parse_play, parse_venue

FIX = Path(__file__).parent / "fixtures"
BASE = "https://biletinial.com/tr-tr/tiyatro/"


def load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def play(name: str, slug: str):
    return parse_play(load(name), BASE + slug)


HIC = "hic-kimsenin-oykusu-kocaeli-buyuksehir-sehir-tiyatrolari"


def test_parse_venue_lists_six_plays():
    refs = parse_venue(load("venue.html"))
    assert {r.slug for r in refs} == {
        "on-ikinci-gece-kocaeli-bb",
        "evvel-zaman-icinde-kocaeli-sehir-tiyatrolari",
        "unutmak-kocaeli-bb",
        HIC,
        "ve-ates-bizi-tuketiyor-kocaeli-bb",
        "kusursuz-dunyaya-yolculuk-kocaeli-bb",
    }
    assert len(refs) == 6
    for r in refs:
        assert "?" not in r.slug and "cinemaBranchId" not in r.url
        assert r.url == BASE + r.slug
    titles = {r.slug: r.title for r in refs}
    assert titles[HIC] == "Hiç Kimsenin Öyküsü"


def test_parse_play_hic_kimsenin():
    p = play("play_hic_kimsenin.html", HIC)
    assert p.slug == HIC
    assert p.title == "Hiç Kimsenin Öyküsü"
    assert p.tags == set()
    assert len(p.sessions) == 2
    by_id = {s.id: s for s in p.sessions}
    a = by_id["18699593"]
    assert a.status == Status.SOLD_OUT
    assert a.start == datetime(2026, 11, 18, 20, 0, tzinfo=ISTANBUL)
    assert a.opens_at is None
    b = by_id["18699594"]
    assert b.status == Status.UPCOMING
    assert b.opens_at == datetime(2026, 11, 13, 10, 0, tzinfo=ISTANBUL)
    assert b.url == BASE + HIC + "/18699594"
    assert b.venue == "SDKM - Oda Tiyatrosu"


def test_json_ld_availability_ignored():
    html = load("play_hic_kimsenin.html")
    assert "schema.org/InStock" in html
    p = parse_play(html, BASE + HIC)
    assert {s.id: s.status for s in p.sessions}["18699594"] == Status.UPCOMING


def test_parse_play_on_ikinci_gece():
    p = play("play_on_ikinci_gece.html", "on-ikinci-gece-kocaeli-bb")
    assert len(p.sessions) == 7
    assert all(s.status == Status.SOLD_OUT for s in p.sessions)
    assert p.genre == "Komedi"
    assert p.duration_min == 130
    assert p.price_from == 35.0
    s = p.sessions[0]
    assert (s.end - s.start).total_seconds() == 130 * 60


def test_parse_play_kusursuz():
    p = play("play_kusursuz.html", "kusursuz-dunyaya-yolculuk-kocaeli-bb")
    assert p.genre == "Fantastik"
    assert p.duration_min == 60
    assert p.age_limit == 4
    assert len(p.sessions) == 1
    # JSON-LD says 17:00 but the page shows 14:00; JSON-LD hour is deliberately ignored.
    assert p.sessions[0].start == datetime(2026, 11, 29, 14, 0, tzinfo=ISTANBUL)
    assert p.sessions[0].end == datetime(2026, 11, 29, 15, 0, tzinfo=ISTANBUL)


def test_visible_time_matches_jsonld_elsewhere():
    p = play("play_on_ikinci_gece.html", "on-ikinci-gece-kocaeli-bb")
    got = [(s.start.month, s.start.day, s.start.hour, s.start.minute) for s in p.sessions]
    assert got == [(11, 12, 20, 0), (11, 13, 20, 0), (11, 14, 15, 0), (11, 14, 20, 0),
                   (11, 19, 20, 0), (11, 20, 20, 0), (11, 21, 20, 0)]


def test_on_sale_PROVISIONAL():
    # Synthetic fixture (button without off/disabled); replace with a real capture later.
    p = play("play_on_sale.html", HIC)
    by_id = {s.id: s for s in p.sessions}
    assert by_id["18699594"].status == Status.ON_SALE
    assert by_id["18699594"].opens_at is None
    assert by_id["18699593"].status == Status.SOLD_OUT


def test_unknown_button_state():
    html = load("play_hic_kimsenin.html").replace(
        'class="cta seanceSelect off sold-out"', 'class="cta seanceSelect weird"'
    )
    p = parse_play(html, BASE + HIC)
    assert {s.id: s.status for s in p.sessions}["18699593"] == Status.UNKNOWN


def test_empty_page_raises():
    with pytest.raises(ParseError):
        parse_play("<html></html>", BASE + "x")
    with pytest.raises(ParseError):
        parse_venue("<html></html>")


def test_session_json_ld_mismatch_raises():
    html = load("play_hic_kimsenin.html").replace("2026-11-18T20:00:00+03:00", "2026-11-19T20:00:00+03:00")
    with pytest.raises(ParseError):
        parse_play(html, BASE + HIC)
