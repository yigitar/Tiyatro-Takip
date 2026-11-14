"""Wave 3a-C: Turkish collation, row sorting, header-click cycle, persistence."""
from __future__ import annotations

import json

import pytest

from pc import app_logic as L
from pc.viewmodel import Row


def test_tr_sort_key_follows_the_turkish_alphabet():
    words = ["Zeytin", "Üzüm", "Ütü", "Uçak", "Şeker", "Sarı", "Ördek", "Oda", "İyi", "Işık",
             "Ilık", "Hava", "Gül", "Ğ", "Fare", "Çocuk", "Cam", "Can", "Ağaç", "Baba", "Ayşe"]
    got = sorted(words, key=L.tr_sort_key)
    assert got == ["Ağaç", "Ayşe", "Baba", "Cam", "Can", "Çocuk", "Fare", "Gül", "Ğ", "Hava",
                   "Ilık", "Işık", "İyi", "Oda", "Ördek", "Sarı", "Şeker", "Uçak", "Ütü",
                   "Üzüm", "Zeytin"]


def test_tr_sort_key_dotted_and_dotless_i_and_case():
    assert L.tr_sort_key("ısı") < L.tr_sort_key("isi")        # ı before i
    assert L.tr_sort_key("Işık") < L.tr_sort_key("İyi")
    assert L.tr_sort_key("IŞIK") == L.tr_sort_key("ışık")     # capital I is dotless
    assert L.tr_sort_key("İYİ") == L.tr_sort_key("iyi")
    assert L.tr_sort_key("cam") < L.tr_sort_key("çam") < L.tr_sort_key("dam")
    assert L.tr_sort_key("ab") < L.tr_sort_key("abc")           # prefix first
    assert L.tr_sort_key("2 Kişi") < L.tr_sort_key("Ali")       # digits before letters
    assert L.tr_sort_key("") == L.tr_sort_key("")


def R(slug, title="T", **kw):
    return Row(slug=slug, title=title, **kw)


def slugs(rows):
    return [r.slug for r in rows]


def test_sort_numeric_columns_and_missing_last_both_directions():
    rows = [R("a", duration="130 dk"), R("b", duration=""), R("c", duration="60 dk"),
            R("d", duration="95 dk"), R("e", duration="")]
    assert slugs(L.sort_rows(rows, ("duration", "asc"))) == ["c", "d", "a", "b", "e"]
    assert slugs(L.sort_rows(rows, ("duration", "desc"))) == ["a", "d", "c", "b", "e"]
    ages = [R("a", age="13+"), R("b", age=""), R("c", age="4+"), R("d", age="7+")]
    assert slugs(L.sort_rows(ages, ("age", "asc"))) == ["c", "d", "a", "b"]
    assert slugs(L.sort_rows(ages, ("age", "desc"))) == ["a", "d", "c", "b"]
    prices = [R("a", price="250 TL"), R("b", price="247,50 TL"), R("c", price=""),
              R("d", price="35 TL")]
    assert slugs(L.sort_rows(prices, ("price", "asc"))) == ["d", "b", "a", "c"]
    assert slugs(L.sort_rows(prices, ("price", "desc"))) == ["a", "b", "d", "c"]


def test_sort_text_columns_turkish_and_missing_last():
    rows = [R("1", "Işık"), R("2", "İyi"), R("3", "Cam"), R("4", "Çocuk"), R("5", "")]
    assert slugs(L.sort_rows(rows, ("title", "asc"))) == ["3", "4", "1", "2", "5"]
    assert slugs(L.sort_rows(rows, ("title", "desc"))) == ["2", "1", "4", "3", "5"]
    tags = [R("a", tags=["gündüz", "çocuk"]), R("b"), R("c", tags=["çocuk"]), R("d", tags=["a"])]
    assert slugs(L.sort_rows(tags, ("tags", "asc"))) == ["d", "c", "a", "b"]
    genre = [R("a", genre="Dram"), R("b", genre=""), R("c", genre="Çocuk Oyunu")]
    assert slugs(L.sort_rows(genre, ("genre", "asc"))) == ["c", "a", "b"]
    assert slugs(L.sort_rows(genre, ("genre", "desc"))) == ["a", "c", "b"]


def test_sort_sessions_status_and_follow():
    rows = [R("a", session_count=2), R("b", session_count=0), R("c", session_count=7)]
    assert slugs(L.sort_rows(rows, ("sessions", "desc"))) == ["c", "a", "b"]
    assert slugs(L.sort_rows(rows, ("sessions", "asc"))) == ["b", "a", "c"]
    st = [R("a", session_count=6, status_summary="1 satışta · 5 tükendi"), R("b", session_count=3, status_summary="2 satışta · 1 tükendi"),
          R("c", session_count=3, status_summary="3 yakında"), R("d", status_summary="seans yok"),
          R("e", session_count=2, status_summary="1 satışta · 1 tükendi")]
    # "satışta" count first, then fewer "tükendi"; sessionless last
    assert slugs(L.sort_rows(st, ("summary", "desc"))) == ["b", "e", "a", "c", "d"]
    assert slugs(L.sort_rows(st, ("summary", "asc")))[-1] == "d"
    fol = [R("a"), R("b", watched=True, active=True), R("c", target_lines=["x"])]
    assert slugs(L.sort_rows(fol, ("follow", "desc"))) == ["b", "c", "a"]
    assert slugs(L.sort_rows(fol, ("follow", "asc"))) == ["a", "c", "b"]


def test_sort_is_stable_and_none_keeps_default_order():
    rows = [R("a", "Aa", duration="60 dk"), R("b", "Bb", duration="60 dk"),
            R("c", "Cc", duration="60 dk")]
    assert slugs(L.sort_rows(rows, ("duration", "asc"))) == ["a", "b", "c"]
    assert slugs(L.sort_rows(rows, ("duration", "desc"))) == ["a", "b", "c"]  # ties keep order
    assert slugs(L.sort_rows(rows, None)) == ["a", "b", "c"]
    assert L.sort_rows(rows, ("nope", "asc")) == rows


def test_header_click_cycle():
    assert L.next_sort(None, "title") == ("title", "asc")
    assert L.next_sort(("title", "asc"), "title") == ("title", "desc")
    assert L.next_sort(("title", "desc"), "title") is None
    assert L.next_sort(("title", "desc"), "genre") == ("genre", "asc")
    for col in ("sessions", "summary", "follow"):  # "most first" on the first click
        assert L.next_sort(None, col) == (col, "desc")
        assert L.next_sort((col, "desc"), col) == (col, "asc")
        assert L.next_sort((col, "asc"), col) is None


def test_heading_text_shows_arrow_on_active_column_only():
    assert L.heading_text("Oyun", "title", ("title", "asc")) == "Oyun ▲"
    assert L.heading_text("Oyun", "title", ("title", "desc")) == "Oyun ▼"
    assert L.heading_text("Tür", "genre", ("title", "asc")) == "Tür"
    assert L.heading_text("Tür", "genre", None) == "Tür"


def test_sort_persists_in_ui_json_and_bad_values_fall_back(tmp_path):
    p = tmp_path / "ui.json"
    assert L.load_sort(p) is None
    L.save_ui(p, {"geometry": None, "beep": True, "sort": ["price", "desc"]})
    assert L.load_sort(p) == ("price", "desc")
    assert L.load_ui(p) == {"geometry": None, "beep": True}  # existing shape unchanged
    for bad in (["price"], ["nope", "asc"], ["price", "sideways"], "price", 5, None, {"a": 1},
                ["price", "asc", "x"], [1, 2]):
        p.write_text(json.dumps({"sort": bad}), encoding="utf-8")
        assert L.load_sort(p) is None, bad
    p.write_text("{not json", encoding="utf-8")
    assert L.load_sort(p) is None
