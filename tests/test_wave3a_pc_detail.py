"""Wave 3a-B: summary / cast / credits in the PC detail pane."""
from __future__ import annotations

import copy

import pytest

from pc.viewmodel import build_rows, credit_lines
from tests.test_pc_viewmodel import CONFIG, NOW, STATE


def _rows(**extra):
    st = copy.deepcopy(STATE)
    st["plays"]["hic-kimsenin"].update(extra)
    return {r.slug: r for r in build_rows(st, CONFIG, set(st["plays"]), set(), NOW)}


def test_row_carries_details():
    r = _rows(summary="Bir konu.", cast=["Ali Can", "Ece Er"],
              credits={"Yazan": "A B", "Yönetmen": "C D", "Dramaturg": "E F"})["hic-kimsenin"]
    assert r.summary == "Bir konu."
    assert r.cast == ["Ali Can", "Ece Er"]
    assert r.credits == [("Yazan", "A B"), ("Yönetmen", "C D"), ("Dramaturg", "E F")]


def test_missing_and_malformed_details_become_empty():
    assert _rows()["hic-kimsenin"].summary == ""
    r = _rows(summary=5, cast="oops", credits=["x"])["hic-kimsenin"]
    assert (r.summary, r.cast, r.credits) == ("", [], [])
    r = _rows(cast=["Ali", 3, None, ""], credits={"Yazan": None, "Y": "Z"})["hic-kimsenin"]
    assert r.cast == ["Ali"] and r.credits == [("Y", "Z")]


def test_credit_lines_are_compact():
    c = [("Yazan", "A B"), ("Yönetmen", "C D"), ("Dramaturg", "E F")]
    assert credit_lines(c) == ["Yazan: A B  ·  Yönetmen: C D", "Dramaturg: E F"]
    assert credit_lines([]) == []


# ---- window (headless Tk) ------------------------------------------------------------------

tk = pytest.importorskip("tkinter")
from tests.test_pc_app_smoke import Box, FakeService, tk_root, ui  # noqa: E402,F401


def _give_details(ui):
    ui.svc._state["plays"]["p1"].update(
        summary="Bu bir konu özetidir.\n\nİkinci paragraf.",
        cast=["Ali Can", "Ece Er (Hamlet)"],
        credits={"Yazan": "A B", "Yönetmen": "C D", "Dramaturg": "E F"})


def test_detail_shows_summary_cast_and_credits(ui):
    _give_details(ui)
    ui.refresh_rows()
    ui.select_slug("p1")
    assert ui.summary_text.get("1.0", "end").strip() == "Bu bir konu özetidir.\n\nİkinci paragraf."
    before = ui.summary_text.get("1.0", "end-1c")           # read-only: keys do not edit it
    ui.summary_text.focus_force()
    ui.summary_text.event_generate("<Key>", keysym="x")
    ui.summary_text.event_generate("<<Paste>>")
    ui.root.update()
    assert ui.summary_text.get("1.0", "end-1c") == before
    assert ui.cast_label.cget("text") == "Ali Can, Ece Er (Hamlet)"
    assert ui.credits_label.cget("text") == "Yazan: A B  ·  Yönetmen: C D\nDramaturg: E F"


def test_detail_without_details_says_kaynakta_bilgi_yok(ui):
    ui.select_slug("p2")      # wave 17: the same placeholder for plays and source events
    assert ui.summary_text.get("1.0", "end").strip() == "Kaynakta bilgi yok"
    assert ui.cast_label.cget("text") == "Kaynakta bilgi yok"
    assert ui.credits_label.cget("text") == "Kaynakta bilgi yok"


def test_detail_pane_is_scrollable_and_rebuilds_only_on_change(ui):
    ui.select_slug("p1")
    assert ui.detail_canvas.winfo_exists() and str(ui.detail_vsb.winfo_class()) == "TScrollbar"
    ui.root.update_idletasks()
    assert ui.detail_canvas.cget("scrollregion")
    pane = ui.session_tree
    ui.refresh_rows()
    assert ui.session_tree is pane  # same details -> no rebuild
    _give_details(ui)
    ui.refresh_rows()
    assert ui.session_tree is not pane  # details are part of the signature
    assert "konu özetidir" in ui.summary_text.get("1.0", "end")
