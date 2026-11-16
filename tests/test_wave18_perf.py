"""Wave 18: UI responsiveness. The list draws only the visible rows, a refresh with unchanged data
rebuilds nothing, the detail pane reuses its widgets, text measurements are cached. Time budgets
are deliberately generous (they only catch order-of-magnitude regressions, not machine noise)."""
from __future__ import annotations

import time
from datetime import datetime

import pytest

tk = pytest.importorskip("tkinter")
from pc import app_logic as L  # noqa: E402
from pc.table import GridTable  # noqa: E402
from pc.viewmodel import with_events  # noqa: E402
from tests.test_pc_app_smoke import Box, FakeService, iso, tk_root, ui  # noqa: E402,F401
from tests.test_wave17_detail import _pump, _setup  # noqa: E402
from tests.test_wave8 import _show  # noqa: E402
from tiyatro import srcevents as S  # noqa: E402,F401
from tiyatro.models import ISTANBUL  # noqa: E402


def _many(ui, n=200):
    st = ui.svc._state["plays"]
    for i in range(n):
        st[f"x{i:03d}"] = {"title": f"Oyun {i:03d}", "url": f"https://example.test/{i}", "tags": [],
                           "sessions": {"a": {"start": iso(10 + i * 0.1), "status": "on_sale"}}}
    ui.refresh_rows()


def _texts(table):
    return [table.body.itemcget(i, "text") for i in table.body.find_all()
            if table.body.type(i) == "text"]


def _table(n=300, height=300):
    root = tk.Tk()
    root.geometry(f"900x{height}+0+0")
    t = GridTable(root, (("a", "A", 100), ("b", "B", 200)), stretch=("b",))
    t.pack(fill="both", expand=True)
    for i in range(n):
        t.insert("", "end", iid=f"r{i}", cells=[L.Cell(f"a{i}"), L.Cell(f"b{i}")])
    root.update()
    return root, t


def test_table_draws_only_visible_rows_and_follows_scrolling():
    root, t = _table()
    try:
        texts = _texts(t)
        assert "a0" in texts and "a299" not in texts
        # visible rows + overscan, far fewer than 300 rows x 2 cells
        assert len(texts) < 2 * (t.body.winfo_height() // t.row_px + 3 + 2 * 10)
        t.yview_moveto(1.0)
        root.update()
        texts = _texts(t)
        assert "a299" in texts and "a0" not in texts
        # the scroll region still covers every row
        assert int(float(t.body.cget("scrollregion").split()[3])) >= 300 * t.row_px
        t.see("r150")
        root.update()
        assert "a150" in _texts(t)
    finally:
        root.destroy()


def test_table_full_draw_of_many_rows_is_fast_enough():
    root, t = _table(n=500)
    try:
        start = time.perf_counter()
        for _ in range(5):
            t.draw()
        assert (time.perf_counter() - start) / 5 < 1.0       # was ~0.12 s per 200 rows unvirtualised
    finally:
        root.destroy()


def test_selection_and_hover_repaint_the_body_only():
    root, t = _table(n=40)
    try:
        heads = [t.header.itemcget(i, "text") for i in t.header.find_all() if t.header.type(i) == "text"]
        before = t.draws
        t.header.delete("all")                       # a body-only repaint must not redraw it
        t.selection_set("r3")
        root.update()
        assert t.draws == before + 1
        assert not t.header.find_all()
        t.draw()
        assert [t.header.itemcget(i, "text") for i in t.header.find_all()
                if t.header.type(i) == "text"] == heads
    finally:
        root.destroy()


def test_matches_compares_iids_cells_and_tags():
    root, t = _table(n=3)
    try:
        specs = [(f"r{i}", [L.Cell(f"a{i}"), L.Cell(f"b{i}")], ()) for i in range(3)]
        assert t.matches(specs)
        assert not t.matches(specs[:2])
        changed = list(specs)
        changed[1] = ("r1", [L.Cell("a1"), L.Cell("X")], ())
        assert not t.matches(changed)
        tagged = list(specs)
        tagged[2] = ("r2", specs[2][1], ("new",))
        assert not t.matches(tagged)
    finally:
        root.destroy()


def test_refresh_with_unchanged_data_rebuilds_nothing(ui):
    _setup(ui)
    _many(ui, 60)
    _show(ui, 1500, 900)
    ui.select_slug("x010")
    _pump(ui)
    draws, fills, builds = ui.tree.draws, ui.detail_fills, ui.detail_builds
    rebuilt, skipped = ui.rows_rebuilt, ui.rows_skipped
    rows_before = ui.tree.get_children()
    for _ in range(5):
        ui.refresh_rows()
        _pump(ui, 3)
    assert ui.rows_rebuilt == rebuilt and ui.rows_skipped == skipped + 5
    assert ui.tree.draws == draws                         # not one repaint of the list
    assert (ui.detail_fills, ui.detail_builds) == (fills, builds)   # nor of the detail
    assert ui.tree.get_children() == rows_before and ui.selected == "x010"


def test_refresh_with_changed_data_rebuilds_and_keeps_selection_and_scroll(ui):
    _setup(ui)
    _many(ui, 60)
    _show(ui, 1500, 900)
    ui.select_slug("x030")
    _pump(ui)
    top = ui.tree.yview()[0]
    ui.svc._state["plays"]["x031"]["title"] = "Başka ad"
    rebuilt = ui.rows_rebuilt
    ui.refresh_rows()
    _pump(ui)
    assert ui.rows_rebuilt == rebuilt + 1
    assert ui.selected == "x030" and ui.tree.selection() == ("x030",)
    assert ui.tree.item("x031", "values")[1] == "Başka ad"
    assert abs(ui.tree.yview()[0] - top) < 0.05


def test_detail_widgets_are_reused_between_rows_and_kinds(ui):
    _setup(ui)
    _many(ui, 10)
    _show(ui, 1500, 900)
    ui.select_slug("x001")
    _pump(ui)
    assert ui.detail_builds == 1
    canvas, tree = ui.detail_canvas, ui.session_tree
    ui.select_slug("x002")
    _pump(ui)
    assert ui.detail_builds == 1 and ui.detail_fills >= 1
    assert ui.detail_canvas is canvas and ui.session_tree is tree
    assert ui.title_label.cget("text") == "Oyun 002"
    ui.select_slug("bk:k")                                   # an event: built once ...
    _pump(ui)
    assert ui.detail_builds == 2 and ui.title_label.cget("text") == "Kitaplar Ülkesi"
    assert not canvas.winfo_ismapped()                       # the play view is parked, not destroyed
    ui.select_slug("x003")                                   # ... and back: nothing is built
    _pump(ui)
    ui.select_slug("bk:u")
    _pump(ui)
    assert ui.detail_builds == 2 and canvas.winfo_exists()
    assert ui.title_label.cget("text") == "Unutmak"


def test_reused_detail_shows_each_row_exactly_like_a_fresh_one(ui):
    """Whatever the previous row was, the next one looks as if built from scratch."""
    _setup(ui)
    _show(ui, 1500, 900)

    def snapshot():
        parts = ui._detail_parts
        out = {"title": ui.title_label.cget("text"), "meta": ui.meta_label.cget("text"),
               "facts": [(w.cget("text")) for w in ui.event_fact_labels],
               "summary": ui.summary_text.cget("text"), "cast": ui.cast_label.cget("text"),
               "credits": ui.credits_label.cget("text"),
               "prov": ui.provenance_label.cget("text") if ui.provenance_label else None,
               "fg": (str(ui.summary_text.cget("foreground")), str(ui.cast_label.cget("foreground"))),
               "targets": [lab.cget("text") for lab in ui._target_labels]}
        out["shown_parts"] = sorted(k for k, w in parts.items() if w is not None and w.winfo_manager())
        return out

    slugs = ["p1", "p2", "unutmak", "bk:u", "bk:k"]
    fresh = {}
    for slug in slugs:                       # each one built from a cold detail
        ui._detail_placeholder()
        ui._detail_sig = None
        ui.select_slug(slug)
        _pump(ui)
        fresh[slug] = snapshot()
    for slug in slugs + slugs[::-1]:         # then in a mixed order, reusing widgets
        ui.select_slug(slug)
        _pump(ui)
        assert snapshot() == fresh[slug], slug


def test_selecting_rows_in_a_big_list_stays_responsive(ui):
    _setup(ui)
    _many(ui, 200)
    _show(ui, 1500, 900)
    slugs = [f"x{i:03d}" for i in range(0, 200, 7)][:20]
    ui.select_slug(slugs[0])
    _pump(ui)
    start = time.perf_counter()
    for slug in slugs:
        ui.select_slug(slug)
        ui.root.update()
    per_select = (time.perf_counter() - start) / len(slugs)
    assert per_select < 1.0, per_select       # measured ~0.1-0.2 s here; was ~0.6 s with 200 rows


def test_wrapped_lines_caches_word_measurements():
    from tkinter import font as tkfont
    from pc.widgets import _WORD_PX, wrapped_lines
    root = tk.Tk()
    try:
        f = tkfont.Font(root=root, family="Segoe UI", size=10)
        calls = []
        real = f.measure
        text = "bu bir deneme metnidir " * 40
        first = wrapped_lines(text, f, 300)
        assert _WORD_PX
        f.measure = lambda s, *a, **k: calls.append(s) or real(s, *a, **k)
        assert wrapped_lines(text, f, 300) == first
        assert calls == []                          # all words came from the cache
        g = tkfont.Font(root=root, family="Segoe UI", size=10)   # a copy of the same font shares it
        g.measure = lambda s, *a, **k: calls.append(s) or real(s, *a, **k)
        assert wrapped_lines(text, g, 300) == first and calls == []
        big = tkfont.Font(root=root, family="Segoe UI", size=20)
        assert wrapped_lines(text, big, 300) > first
    finally:
        root.destroy()
