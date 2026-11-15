"""Wave 4 A2: Kitle (audience) and Tür (genre) are separate, derived and sortable."""
from pc import app_logic as L
from pc.viewmodel import Row, _row
from tiyatro.config import default_config
from tiyatro.tags import audience_of, genre_label


def test_audience_from_genre_wording_then_tags_then_age():
    assert audience_of("Çocuk Oyunu") == "Çocuk"
    assert audience_of("Yetişkin Oyunu") == "Yetişkin"
    assert audience_of("YETİŞKİN OYUNU", tags=["çocuk"]) == "Yetişkin"  # page wording wins
    assert audience_of("Komedi", tags=["çocuk"]) == "Çocuk"
    assert audience_of("Komedi", age_limit=7) == "Çocuk"
    assert audience_of("Komedi", age_limit=13) == "Yetişkin"
    assert audience_of("Komedi") == "" and audience_of(None) == ""
    assert audience_of("Çocuk ve Yetişkin Oyunu", age_limit=16) == "Yetişkin"  # ambiguous -> age


def test_genre_never_contains_the_audience():
    assert genre_label("Komedi") == "Komedi"
    assert genre_label("Yetişkin Oyunu") == "" and genre_label("Çocuk Oyunu") == ""
    assert genre_label("ÇOCUK TİYATROSU") == "" and genre_label(None) == ""
    assert genre_label("Fantastik") == "Fantastik"
    assert genre_label("Çocuk Müzikali") == "Müzikali"


def _entry(genre, tags=(), age=None):
    return {"title": "Oyun", "genre": genre, "tags": list(tags), "age_limit": age,
            "sessions": {}}


def test_row_has_separate_kitle_and_tur():
    from datetime import datetime
    from tiyatro.models import ISTANBUL
    now = datetime(2026, 11, 15, tzinfo=ISTANBUL)
    cfg = default_config()
    r = _row("p", _entry("Yetişkin Oyunu", age=16), cfg, set(), set(), now)
    assert (r.audience, r.genre) == ("Yetişkin", "Tiyatro")  # genre blank -> stated fallback
    r = _row("p", _entry("Komedi", tags=["çocuk"], age=7), cfg, set(), set(), now)
    assert (r.audience, r.genre) == ("Çocuk", "Komedi")
    values = dict(zip(L.SORT_COLUMNS, L.row_values(r)))
    assert (values["audience"], values["genre"]) == ("Çocuk", "Komedi")


def test_meta_line_shows_both():
    assert L.meta_line(Row("p", "T", audience="Çocuk", genre="Komedi", duration="90 dk",
                           age="7+")) == "Kitle: Çocuk  ·  Tür: Komedi  ·  Süre: 90 dk  ·  Yaş: 7+"
    assert "Kitle: Bilgi yok  ·  Tür: Bilgi yok" == L.meta_line(Row("p", "T"))


def test_kitle_sortable_missing_last():
    rows = [Row("a", "A", audience="Yetişkin"), Row("b", "B"), Row("c", "C", audience="Çocuk")]
    assert [r.slug for r in L.sort_rows(rows, ("audience", "asc"))] == ["c", "a", "b"]
    assert [r.slug for r in L.sort_rows(rows, ("audience", "desc"))] == ["a", "c", "b"]
    assert "audience" in L.SORT_COLUMNS
