"""Wave 17: Başiskele / VKV events borrow Özet / Oyuncular / Yapım / Süre / Yaş / Tür from the ONE
Biletinial play with exactly the same normalised title (marked as borrowed, never when ambiguous)."""
from __future__ import annotations

from datetime import datetime

from pc.viewmodel import event_row
from tiyatro import srcevents as S
from tiyatro.config import config_from_dict
from tiyatro.models import ISTANBUL

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=ISTANBUL)


def play(title, **kw):
    entry = {"title": title, "genre": "Komedi", "summary": "Bir aile, bir gece.\r\n\r\n\r\nİkinci.",
             "cast": ["Ali Can", "Ece Er"], "credits": {"Yazan": "A B", "Yönetmen": "C D"},
             "duration_min": 95, "age_limit": 13, "sessions": {}}
    entry.update(kw)
    return entry


def state_with(plays, events):
    state = {"plays": plays, "source_events": {}}
    for eid, (title, extra) in events.items():
        rec = {"id": eid, "source": "basiskele", "title": title, "kind": "Tiyatro Oyunu",
               "start": "2026-11-17T20:00:00+03:00", "venue": "", "url": "u"}
        rec.update(extra)
        state["source_events"][eid] = rec
    return state


def views(state):
    return {v["id"]: v for v in S.event_views(state, config_from_dict({}), NOW)}


def test_norm_title_folds_case_turkish_letters_and_punctuation():
    n = S.norm_title
    assert n("Unutmak") == n("UNUTMAK") == n("  unutmak! ") == "unutmak"
    assert n("Hiç Kimsenin Öyküsü") == n("hic kimsenin oykusu")
    assert n("Sizi Bir Yerden Seviyor Muyum?") == n("sizi bir yerden seviyor muyum")
    assert n("") == "" and n("???") == ""


def test_exact_single_match_fills_empty_fields_and_marks_them_borrowed():
    st = state_with({"unutmak-kbb": play("Unutmak")}, {"bk:1": ("UNUTMAK", {})})
    v = views(st)["bk:1"]
    assert v["info"]["summary"].startswith("Bir aile")
    assert v["info"]["cast"] == ["Ali Can", "Ece Er"]
    assert v["info"]["credits"] == [("Yazan", "A B"), ("Yönetmen", "C D")]
    assert (v["info"]["duration"], v["info"]["age"]) == ("95 dk", "13+")
    assert v["genre"] == "Komedi" and v["genre_borrowed"] is True
    assert set(v["borrowed"]) == {"summary", "cast", "credits", "duration", "age", "genre"}
    assert v["borrowed_credit_labels"] == ["Yazan", "Yönetmen"]
    assert v["borrowed_from"] == {"slug": "unutmak-kbb", "title": "Unutmak"}


def test_own_stated_values_win_and_are_not_marked():
    own = {"info": {"summary": "Belediye metni.", "cast": ["Zeynep Z"], "duration_min": 80,
                    "credits": {"Sahneleyen": "Kocaeli Şehir Tiyatroları"}}}
    st = state_with({"u": play("Unutmak")}, {"bk:1": ("Unutmak", own)})
    v = views(st)["bk:1"]
    assert v["info"]["summary"] == "Belediye metni." and v["info"]["cast"] == ["Zeynep Z"]
    assert v["info"]["duration"] == "80 dk"
    assert set(v["borrowed"]) == {"credits", "age", "genre"}            # only what was missing
    # own credit entry first (normal), the play's entries after it (borrowed, per label)
    assert v["info"]["credits"] == [("Sahneleyen", "Kocaeli Şehir Tiyatroları"),
                                    ("Yazan", "A B"), ("Yönetmen", "C D")]
    assert v["borrowed_credit_labels"] == ["Yazan", "Yönetmen"]


def test_ambiguous_title_is_never_borrowed():
    st = state_with({"a": play("Unutmak"), "b": play("unutmak!", summary="Başka.")},
                    {"bk:1": ("Unutmak", {})})
    v = views(st)["bk:1"]
    assert v["borrowed"] == [] and v["borrowed_from"] is None and v["info"]["summary"] == ""


def test_no_match_and_near_matches_borrow_nothing():
    st = state_with({"a": play("Unutmak Kolay Değil")}, {"bk:1": ("Unutmak", {})})
    v = views(st)["bk:1"]
    assert v["borrowed"] == [] and v["borrowed_from"] is None


def test_empty_title_never_matches():
    st = state_with({"a": play("???")}, {"bk:1": ("!!!", {})})
    assert views(st)["bk:1"]["borrowed"] == []


def test_play_without_data_borrows_only_what_exists():
    st = state_with({"a": {"title": "Unutmak", "sessions": {}, "summary": "Sadece özet."}},
                    {"bk:1": ("Unutmak", {})})
    v = views(st)["bk:1"]
    assert v["borrowed"] == ["summary"] and v["borrowed_from"]["slug"] == "a"


def test_row_carries_borrowed_marks_and_provenance():
    st = state_with({"a": play("Unutmak")}, {"bk:1": ("Unutmak", {})})
    row = event_row(views(st)["bk:1"], NOW)
    assert row.summary.startswith("Bir aile") and row.cast == ["Ali Can", "Ece Er"]
    assert row.borrowed == frozenset({"summary", "cast", "credits", "duration", "age", "genre"})
    assert row.borrowed_credits == frozenset({"Yazan", "Yönetmen"})
    assert row.borrowed_from == "Unutmak"
    assert "genre" in row.derived
    assert row.duration == "95 dk" and row.age == "13+"
    # the "summary" derived key (status column) is unrelated; borrowed is its own set
    plain = event_row(views(state_with({}, {"bk:2": ("Unutmak", {})}))["bk:2"], NOW)
    assert plain.borrowed == frozenset() and plain.borrowed_from == ""


def test_records_in_state_are_not_changed_by_viewing():
    st = state_with({"a": play("Unutmak")}, {"bk:1": ("Unutmak", {})})
    before = repr(st)
    views(st)
    assert repr(st) == before
