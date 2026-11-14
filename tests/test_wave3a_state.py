"""Wave 3a: summary / cast / credits are stored per play and survive thin parses."""
import copy
import dataclasses

from tests.test_state import T0 as NOW, NOWATCH, WATCH, play, sess
from tiyatro.runner import _fingerprint
from tiyatro.state import diff, empty_state


def rich(**kw):
    p = play(sessions=[sess()])
    return dataclasses.replace(p, summary="Konu metni.", cast=["Ayşe Can", "Ali Veli"],
                               credits={"Yazan": "Ece Er", "Yönetmen": "Can Yıl"}, **kw)


def test_details_are_stored():
    st, ev = diff(empty_state(), [rich()], NOWATCH, NOW)
    e = st["plays"]["p"]
    assert e["summary"] == "Konu metni."
    assert e["cast"] == ["Ayşe Can", "Ali Veli"]
    assert e["credits"] == {"Yazan": "Ece Er", "Yönetmen": "Can Yıl"}


def test_thin_parse_keeps_previous_details():
    st, _ = diff(empty_state(), [rich()], NOWATCH, NOW)
    st2, _ = diff(st, [play(sessions=[sess()])], NOWATCH, NOW)
    e = st2["plays"]["p"]
    assert e["summary"] == "Konu metni." and e["cast"] == ["Ayşe Can", "Ali Veli"]
    assert e["credits"]["Yazan"] == "Ece Er"


def test_new_play_without_details_gets_empty_defaults():
    st, _ = diff(empty_state(), [play(sessions=[sess()])], NOWATCH, NOW)
    e = st["plays"]["p"]
    assert e["summary"] is None and e["cast"] == [] and e["credits"] == {}


def test_changed_summary_changes_state_but_never_alerts():
    st, _ = diff(empty_state(), [rich()], WATCH, NOW)
    p2 = dataclasses.replace(rich(), summary="Yeni konu.")
    st2, ev = diff(st, [p2], WATCH, NOW)
    assert st2["plays"]["p"]["summary"] == "Yeni konu."
    assert [e.kind for e in ev] == []
    assert _fingerprint(st) != _fingerprint(st2)


def test_unchanged_details_do_not_change_fingerprint():
    st, _ = diff(empty_state(), [rich()], WATCH, NOW)
    st2, _ = diff(copy.deepcopy(st), [rich()], WATCH, NOW)
    assert _fingerprint(st) == _fingerprint(st2)


def test_old_schema_state_without_details_keeps_working():
    st, _ = diff(empty_state(), [play(sessions=[sess()])], NOWATCH, NOW)
    for k in ("summary", "cast", "credits"):
        st["plays"]["p"].pop(k)
    st2, ev = diff(st, [rich()], NOWATCH, NOW)
    assert st2["plays"]["p"]["summary"] == "Konu metni."
    st3, _ = diff(st, [play(sessions=[sess()])], NOWATCH, NOW)
    assert st3["plays"]["p"]["cast"] == []
