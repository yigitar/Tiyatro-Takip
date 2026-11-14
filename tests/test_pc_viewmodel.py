from __future__ import annotations

from datetime import datetime

from pc.viewmodel import Row, build_rows, format_event_for_desktop
from tiyatro.config import config_from_dict
from tiyatro.models import ISTANBUL, Event

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=ISTANBUL)  # Friday


def _s(status, start, opens_at=None, gone=False):
    return {"status": status, "start": start, "opens_at": opens_at, "url": "", "gone": gone}


STATE = {
    "plays": {
        "hic-kimsenin": {
            "title": "Hiç Kimsenin Kimsesi", "url": "https://biletinial.com/tr-tr/tiyatro/hk",
            "genre": "Dram", "duration_min": 130, "age_limit": 13, "price_from": 250.0,
            "tags": [], "removed": False,
            "sessions": {
                "a1": _s("on_sale", "2026-11-20T20:00:00+03:00"),
                "a2": _s("upcoming", "2026-11-27T20:00:00+03:00", "2026-11-16T10:00:00+03:00"),
                "a3": _s("upcoming", "2026-11-28T20:00:00+03:00", "2026-11-17T10:00:00+03:00"),
                "a4": _s("sold_out", "2026-11-14T20:00:00+03:00"),
                "a5": _s("sold_out", "2026-11-15T20:00:00+03:00"),
                "a6": _s("sold_out", "2026-11-16T20:00:00+03:00"),
                "a7": _s("on_sale", "2026-11-18T20:00:00+03:00", gone=True),
            },
        },
        "kucuk-prens": {
            "title": "Küçük Prens", "url": "https://biletinial.com/tr-tr/tiyatro/kp",
            "genre": "Çocuk", "duration_min": 60, "age_limit": 4, "price_from": 20.0,
            "tags": ["gündüz", "çocuk"], "removed": False,
            "sessions": {"k1": _s("sold_out", "2026-11-14T11:00:00+03:00")},
        },
        "eski-oyun": {
            "title": "Eski Oyun", "url": "https://biletinial.com/tr-tr/tiyatro/eo",
            "genre": None, "duration_min": None, "age_limit": None, "price_from": None,
            "tags": [], "removed": True,
            "sessions": {"e1": _s("sold_out", "2026-10-01T20:00:00+03:00")},
        },
        "yeni-oyun": {
            "title": "Yeni Oyun", "url": "https://biletinial.com/tr-tr/tiyatro/yo",
            "genre": "Komedi", "duration_min": 95, "age_limit": None, "price_from": 247.5,
            "tags": [], "removed": False,
            "sessions": {"y1": _s("upcoming", "2026-11-22T20:00:00+03:00")},
        },
    },
    "meta": {"last_scan": NOW.isoformat()}, "emitted": {}, "outbox": [],
}

CONFIG = config_from_dict({"plays": {
    "hic-kimsenin": {"active": True, "targets": [
        {"id": "y", "name": "Yetişkin", "people": 2,
         "rule": {"days": [4, 5], "from_time": "18:00", "to_time": None,
                  "overrides": {"5": {"from": "14:00", "to": None}},
                  "exclude_dates": ["2026-11-28"]}}]},
    "kucuk-prens": {"active": False, "targets": [],
                    "tag_overrides": {"gündüz": False}},
}})


def rows():
    return build_rows(STATE, CONFIG, seen_slugs={"hic-kimsenin", "kucuk-prens", "eski-oyun"},
                      removed_slugs={"eski-oyun"}, now=NOW)


def by_slug():
    return {r.slug: r for r in rows()}


def test_row_fields_turkish_text():
    r = by_slug()["hic-kimsenin"]
    assert isinstance(r, Row)
    assert r.title == "Hiç Kimsenin Kimsesi"
    assert r.genre == "Dram" and r.duration == "130 dk" and r.age == "13+"
    assert r.price == "250 TL"
    assert r.session_count == 6  # gone session not counted
    assert r.status_summary == "1 satışta · 2 yakında · 3 tükendi"
    assert r.next_opening == "16 Kasım Pazartesi 10:00"
    assert r.watched is True and r.active is True
    assert r.target_lines == ["Yetişkin: 2 kişi · Cum, Cmt · 18:00 sonrası · "
                              "1 gün istisnası · 1 hariç tarih"]
    assert r.url == "https://biletinial.com/tr-tr/tiyatro/hk"
    assert r.is_new is False and r.is_removed is False


def test_price_decimals_and_missing_metadata():
    rs = by_slug()
    assert rs["yeni-oyun"].price == "247,50 TL"
    assert rs["yeni-oyun"].age == ""
    assert rs["yeni-oyun"].next_opening == ""  # upcoming without announced opening
    assert rs["yeni-oyun"].status_summary == "1 yakında"
    eo = rs["eski-oyun"]
    assert (eo.genre, eo.duration, eo.age, eo.price) == ("", "", "", "")
    assert eo.status_summary == "seans yok"  # only a past session


def test_tags_respect_config_overrides():
    rs = by_slug()
    assert rs["kucuk-prens"].tags == ["çocuk"]  # gündüz switched off by tag_overrides
    assert rs["kucuk-prens"].watched is False and rs["kucuk-prens"].target_lines == []


def test_new_and_removed_flags():
    rs = by_slug()
    assert rs["yeni-oyun"].is_new is True
    assert rs["eski-oyun"].is_removed is True


def test_order_watched_first_then_nearest_session():
    assert [r.slug for r in rows()] == ["hic-kimsenin", "kucuk-prens", "yeni-oyun",
                                        "eski-oyun"]


def test_watched_play_missing_from_state_still_listed():
    cfg = config_from_dict({"plays": {"bilinmeyen": {"active": True, "targets": []}}})
    out = build_rows({"plays": {}}, cfg, set(), set(), NOW)
    assert [(r.slug, r.title, r.watched, r.target_lines) for r in out] == [
        ("bilinmeyen", "bilinmeyen", True, ["hedef yok"])]


def test_format_event_for_desktop_plain_text():
    ev = Event("ON_SALE", "ON_SALE:a1:1", "hic-kimsenin", "a1", ["y"],
               {"title": "Hiç Kimsenin <Kimsesi>", "url": "https://biletinial.com/s/a1",
                "start": "2026-11-20T20:00:00+03:00", "status": "on_sale"})
    title, text = format_event_for_desktop(ev, {}, CONFIG)
    assert "Bilet alınabilir" in title
    assert "Hiç Kimsenin <Kimsesi>" in text  # unescaped, no HTML tags
    assert "<b>" not in text and "&lt;" not in text
    assert "20 Kasım Cuma 20:00" in text
    assert "Yetişkin (2 kişi)" in text
    assert "https://biletinial.com/s/a1" in text


def test_target_summary_to_time_says_kadar():  # final review C2
    from tiyatro.models import Rule, Target
    from pc.viewmodel import target_summary
    assert target_summary(Target("y", "Yetişkin", 2, Rule(to_time="20:00"))) == \
        "Yetişkin: 2 kişi · her gün · 20:00'ye kadar"
    assert target_summary(Target("y", "Yetişkin", 2, Rule(to_time="18:00"))).endswith(
        "18:00'e kadar")
    assert target_summary(Target("y", "Yetişkin", 2, Rule(to_time="22:30"))).endswith(
        "22:30'a kadar")
