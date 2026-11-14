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


def test_on_sale_synthetic():
    # Synthetic fixture: button without off/disabled/data-acilis.
    p = play("play_on_sale.html", HIC)
    by_id = {s.id: s for s in p.sessions}
    assert by_id["18699594"].status == Status.ON_SALE
    assert by_id["18699594"].opens_at is None
    assert by_id["18699593"].status == Status.SOLD_OUT


def test_on_sale_real():
    p = play("play_on_sale_real.html", HIC)
    by_id = {s.id: s for s in p.sessions}
    s = by_id["18699594"]
    assert s.status == Status.ON_SALE
    assert s.opens_at is None
    assert s.start == datetime(2026, 11, 25, 20, 0, tzinfo=ISTANBUL)
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


# ---- synthetic-page helpers -------------------------------------------------

def _row(sid, day, month_tr, hh, mm, button, row_open='<div class="row" id="seans-{sid}">'):
    return (
        row_open.format(sid=sid)
        + f'<span class="d">{day} {month_tr} Cuma</span><span class="tm">{hh}:{mm}</span>'
        + '<div class="vl"><a href="/m">Salon &amp; Sahne</a> · X</div>'
        + f'<div class="right">{button}</div></div>'
    )


def _event(start, end=None, offers=None):
    ev = {"@type": "Event", "startDate": start, "location": {"name": "Loc"}}
    if end:
        ev["endDate"] = end
    if offers is not None:
        ev["offers"] = offers
    return ev


def _page(rows, events, title="Test", script_open='<script type="application/ld+json">', section=True):
    import json as _j

    body = "".join(rows) + ("</section>" if section else "")
    return (
        f"<html><h1 class=\"hero-v3__title\">{title}</h1>{script_open}{_j.dumps(events)}</script>"
        f"<section>{body}</html>"
    )


SOLD = '<button class="cta seanceSelect off sold-out" disabled>TÜKENDİ</button>'
OPEN_BTN = '<button type="submit" class="cta seanceSelect">Bilet Al</button>'
U = BASE + "x"


def test_missing_button_is_unknown():
    p = parse_play(_page([_row(1, 5, "Kasım", "20", "00", "")], [_event("2026-11-05T20:00:00+03:00")]), U)
    assert p.sessions[0].status == Status.UNKNOWN


def test_data_acilis_on_non_off_button_is_on_sale():  # final review I6 (was UNKNOWN)
    btn = '<button class="cta seanceSelect" data-acilis="202611130700">Bilet Al</button>'
    p = parse_play(_page([_row(1, 5, "Kasım", "20", "00", btn)], [_event("2026-11-05T20:00:00+03:00")]), U)
    assert p.sessions[0].status == Status.ON_SALE
    assert p.sessions[0].opens_at == datetime(2026, 11, 13, 10, 0, tzinfo=ISTANBUL)


@pytest.mark.parametrize("btn", [
    '<button class="cta seanceSelect active">Bilet Al</button>',
    '<button class="seanceSelect">Bilet Al</button>',
    '<button class="btn SEANCESELECT cta">Bilet Al</button>',
])
def test_enabled_seanceselect_with_extra_classes_is_on_sale(btn):  # final review I6
    p = parse_play(_page([_row(1, 5, "Kasım", "20", "00", btn)], [_event("2026-11-05T20:00:00+03:00")]), U)
    assert p.sessions[0].status == Status.ON_SALE


@pytest.mark.parametrize("btn", [
    '<button class="cta seanceSelect" disabled>Bilet Al</button>',
    '<button class="cta seanceSelect off">Yakında</button>',
    '<button class="cta buy">Bilet Al</button>',
])
def test_disabled_off_or_unrecognised_buttons_stay_unknown(btn):
    p = parse_play(_page([_row(1, 5, "Kasım", "20", "00", btn)], [_event("2026-11-05T20:00:00+03:00")]), U)
    assert p.sessions[0].status == Status.UNKNOWN


def test_unknown_button_keeps_a_parseable_data_acilis():
    btn = '<button class="cta seanceSelect off" data-acilis="202611130700">Yakında</button>'
    p = parse_play(_page([_row(1, 5, "Kasım", "20", "00", btn)], [_event("2026-11-05T20:00:00+03:00")]), U)
    assert p.sessions[0].status == Status.UNKNOWN
    assert p.sessions[0].opens_at == datetime(2026, 11, 13, 10, 0, tzinfo=ISTANBUL)


def test_december_page_with_january_session():
    rows = [_row(1, 30, "Aralık", "20", "00", SOLD), _row(2, 3, "Ocak", "19", "30", OPEN_BTN)]
    evs = [_event("2026-12-30T20:00:00+03:00", "2026-12-30T21:00:00+03:00"),
           _event("2027-01-03T22:30:00+03:00", "2027-01-03T23:30:00+03:00")]
    p = parse_play(_page(rows, evs), U)
    assert p.sessions[0].start == datetime(2026, 12, 30, 20, 0, tzinfo=ISTANBUL)
    assert p.sessions[1].start == datetime(2027, 1, 3, 19, 30, tzinfo=ISTANBUL)
    assert p.sessions[1].end == datetime(2027, 1, 3, 20, 30, tzinfo=ISTANBUL)
    assert p.sessions[1].status == Status.ON_SALE


def test_entity_escaped_title_and_venue():
    p = parse_play(_page([_row(1, 5, "Kasım", "20", "00", SOLD)], [_event("2026-11-05T20:00:00+03:00")],
                         title="Ali &amp; Veli"), U)
    assert p.title == "Ali & Veli"
    assert p.sessions[0].venue == "Salon & Sahne"


@pytest.mark.parametrize(
    "offers, expected",
    [
        ([{"price": 50}, {"price": "35,00"}], 35.0),
        ({"price": "35.00"}, 35.0),
        ({"price": 40.5}, 40.5),
        ({"price": "abc"}, None),
        (None, None),
        ("weird", None),
        ([{"price": None}, "x", {"price": "60"}], 60.0),
    ],
)
def test_price_forms(offers, expected):
    p = parse_play(_page([_row(1, 5, "Kasım", "20", "00", SOLD)],
                         [_event("2026-11-05T20:00:00+03:00", offers=offers)]), U)
    assert p.price_from == expected


def test_price_is_minimum_over_events():
    rows = [_row(1, 5, "Kasım", "20", "00", SOLD), _row(2, 6, "Kasım", "20", "00", SOLD)]
    evs = [_event("2026-11-05T20:00:00+03:00", offers={"price": 80}),
           _event("2026-11-06T20:00:00+03:00", offers={"price": "45,50"})]
    assert parse_play(_page(rows, evs), U).price_from == 45.5


@pytest.mark.parametrize(
    "row_open",
    [
        '<div id="seans-{sid}" class="row">',
        '<div class="row extra" id="seans-{sid}" data-x="1">',
        "<div  id='seans-{sid}'   class='row'>",
        '<DIV CLASS="row" ID="seans-{sid}">',
    ],
)
def test_row_attribute_variants(row_open):
    rows = [_row(7, 5, "Kasım", "20", "00", SOLD, row_open=row_open)]
    p = parse_play(_page(rows, [_event("2026-11-05T20:00:00+03:00")]), U)
    assert [s.id for s in p.sessions] == ["7"]


@pytest.mark.parametrize(
    "script_open",
    [
        '<script type="application/ld+json" data-n="1">',
        '<script data-x=y type=\'application/ld+json\'>',
        '<SCRIPT  TYPE="application/ld+json" >',
    ],
)
def test_json_ld_script_variants(script_open):
    p = parse_play(_page([_row(1, 5, "Kasım", "20", "00", SOLD)], [_event("2026-11-05T20:00:00+03:00")],
                         script_open=script_open), U)
    assert len(p.sessions) == 1


def test_button_attribute_variants():
    btn = "<button data-acilis='202611130700' DISABLED class='seanceSelect  off cta'>Yakında</button>"
    p = parse_play(_page([_row(1, 5, "Kasım", "20", "00", btn)], [_event("2026-11-05T20:00:00+03:00")]), U)
    assert p.sessions[0].status == Status.UPCOMING
    assert p.sessions[0].opens_at == datetime(2026, 11, 13, 10, 0, tzinfo=ISTANBUL)


def test_missing_section_terminator_stops_at_next_row():
    rows = [_row(1, 5, "Kasım", "20", "00", SOLD), _row(2, 6, "Kasım", "20", "00", OPEN_BTN)]
    evs = [_event("2026-11-05T20:00:00+03:00"), _event("2026-11-06T20:00:00+03:00")]
    p = parse_play(_page(rows, evs, section=False), U)
    assert [s.status for s in p.sessions] == [Status.SOLD_OUT, Status.ON_SALE]


def test_venue_excludes_recommended_cards_without_branch_query():
    html = load("venue.html").replace(
        "</body>",
        '<a class="hpv2-event-card__visual" href="/tr-tr/tiyatro/baska-oyun-ankara" title="Baska">x</a></body>',
    )
    assert "baska-oyun-ankara" in html
    slugs = {r.slug for r in parse_venue(html)}
    assert "baska-oyun-ankara" not in slugs and len(slugs) == 6


def test_venue_falls_back_to_all_links_without_branch_query():
    html = '<a href="/tr-tr/tiyatro/a-oyun">A</a><a href="https://biletinial.com/tr-tr/tiyatro/b-oyun">B</a>'
    assert {r.slug for r in parse_venue(html)} == {"a-oyun", "b-oyun"}


def test_last_row_not_polluted_by_footer_when_section_missing():
    no_venue = _row(2, 6, "Kasım", "20", "00", "").replace('<div class="vl"><a href="/m">Salon &amp; Sahne</a> · X</div>', "")
    no_btn = no_venue.replace('<div class="right"></div>', '<div class="right"></div>')
    rows = [_row(1, 5, "Kasım", "20", "00", SOLD), no_btn]
    evs = [_event("2026-11-05T20:00:00+03:00"), _event("2026-11-06T20:00:00+03:00")]
    footer = (
        '<div class="footer"><span class="d">1 Aralık Salı</span><span class="tm">09:00</span>'
        '<div class="vl"><a href="/z">Decoy Venue</a></div>'
        '<button class="cta seanceSelect off sold-out" disabled>X</button></div>'
    )
    html = _page(rows, evs, section=False).replace("</html>", footer + "</html>")
    p = parse_play(html, U)
    last = p.sessions[-1]
    # last row has no button/venue of its own: footer decoys must not leak in
    assert last.status == Status.UNKNOWN
    assert last.start == datetime(2026, 11, 6, 20, 0, tzinfo=ISTANBUL)
    assert last.venue == "Loc"


def test_valueless_data_acilis_on_enabled_button_is_on_sale():  # final review I6
    btn = '<button class="cta seanceSelect" data-acilis>Bilet Al</button>'
    p = parse_play(_page([_row(1, 5, "Kasım", "20", "00", btn)], [_event("2026-11-05T20:00:00+03:00")]), U)
    assert p.sessions[0].status == Status.ON_SALE
    assert p.sessions[0].opens_at is None


@pytest.mark.parametrize("bad", ["inf", "Infinity", "nan"])
def test_price_rejects_non_finite(bad):
    p = parse_play(_page([_row(1, 5, "Kasım", "20", "00", SOLD)],
                         [_event("2026-11-05T20:00:00+03:00", offers={"price": bad})]), U)
    assert p.price_from is None
