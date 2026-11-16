"""Wave 13: resizable / reorderable / hideable list columns (real mapped window, header drags
simulated with ``event_generate`` on the canvas), fit to the available width, persistence in
the PC's local ``ui.json``."""
from __future__ import annotations

import json

import pytest

from pc import app_logic as L
from pc import ui as appmod
from pc.table import RESET_LABEL, reconcile_layout

tk = pytest.importorskip("tkinter")
from tests.test_pc_app_smoke import Box, FakeService, tk_root, ui  # noqa: E402,F401
from tests.test_wave8 import FakeWin, _show  # noqa: E402,F401
from tests.test_wave9 import _many_plays  # noqa: E402

KEYS = [c[0] for c in appmod.COLUMNS]


def _pt(table, x, y=None):
    return {"x": int(x), "y": table.head_px // 2 if y is None else y}


def press(table, x):
    table.header.event_generate("<ButtonPress-1>", **_pt(table, x))


def move(table, x):
    table.header.event_generate("<B1-Motion>", **_pt(table, x))
    table.update()


def release(table, x):
    table.header.event_generate("<ButtonRelease-1>", **_pt(table, x))
    table.update()


def drag(table, x0, x1, steps=6):
    press(table, x0)
    for n in range(1, steps + 1):
        move(table, x0 + (x1 - x0) * n / steps)
    release(table, x1)
    table.update()
    table.draw()


def dblclick(table, x):
    press(table, x)
    release(table, x)
    press(table, x)          # second press within the double-click time = <Double-Button-1>
    release(table, x)
    table.update()
    table.draw()


def xs_of(table):
    return table._layout()[1]


def centre(table, key):
    xs = xs_of(table)
    i = table.order.index(key)
    return (xs[i] + xs[i + 1]) // 2


def border(table, key):
    return xs_of(table)[table.order.index(key) + 1]


def fresh(ui, width=1760, height=1015):
    _many_plays(ui)
    ui.update_status()
    _show(ui, width, height)
    ui.tree.draw()
    return ui.tree


def ui_json(ui):
    return json.loads((ui.local_dir / "ui.json").read_text(encoding="utf-8"))


# ---- pure helpers -------------------------------------------------------------------------------

def test_reconcile_drops_unknown_appends_missing_at_default_place_and_keeps_one_visible():
    default = ["a", "b", "c", "d"]
    assert reconcile_layout(default, ["c", "zzz", "a", "c"], ["q"]) == (["c", "d", "a", "b"], set())
    assert reconcile_layout(default, None, None) == (default, set())
    assert reconcile_layout(default, "junk", 5) == (default, set())
    order, hidden = reconcile_layout(default, ["d", "c", "b", "a"], ["a", "b", "c", "d"])
    assert order == ["d", "c", "b", "a"] and hidden == {"c", "b", "a"}      # one stays
    assert reconcile_layout(default, ["a", "c", "d"], []) [0] == ["a", "b", "c", "d"]


def test_load_columns_sanitises(tmp_path):
    p = tmp_path / "ui.json"
    p.write_text(json.dumps({"columns": {"order": ["a", 3, "b"], "hidden": "x",
                                         "widths": {"a": 90, "b": "wide", "c": -5, "d": True}}}),
                 encoding="utf-8")
    assert L.load_columns(p) == {"order": ["a", "b"], "widths": {"a": 90}}
    p.write_text("not json", encoding="utf-8")
    assert L.load_columns(p) == {}
    assert L.load_columns(tmp_path / "missing.json") == {}


# ---- fit ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("width,height", [(1760, 1015), (1500, 850)])
def test_default_widths_fit_the_list_without_clipping_the_last_column(ui, width, height):
    t = fresh(ui, width, height)
    try:
        avail = t.body.winfo_width()
        widths, xs = t._layout()
        assert xs[-1] <= avail, (xs[-1], avail)           # last column fully inside
        assert t.hbar.winfo_manager() == ""               # so no horizontal bar
        assert all(w >= t.min_width(k) for w, k in zip(widths, t.order))
        assert t.order == KEYS
        assert abs(xs[-1] - avail) <= 2                   # and no gap either
    finally:
        ui.root.withdraw()


def test_narrow_window_scrolls_horizontally_only_when_even_minimums_do_not_fit(ui):
    t = fresh(ui, 1000, 650)
    try:
        avail = t.body.winfo_width()
        widths, xs = t._layout()
        need = sum(t.min_width(k) for k in t.order)
        if need > avail:
            assert xs[-1] == need and t.hbar.winfo_manager() == "pack"
            t.body.xview_moveto(1.0)
            t._xview("moveto", 1.0)
            t.update()
            assert t.header.xview()[0] == pytest.approx(t.body.xview()[0], abs=0.01)
        else:
            assert xs[-1] <= avail and t.hbar.winfo_manager() == ""
        assert t.header.winfo_width() == t.body.winfo_width()
    finally:
        ui.root.withdraw()


def test_user_widths_beyond_the_viewport_show_the_scrollbar_and_header_scrolls_with_body(ui):
    t = fresh(ui)
    try:
        for key in ("sessions", "rez", "summary"):
            t._user_w[key] = 600
        t.draw()
        t.update()
        assert xs_of(t)[-1] > t.body.winfo_width()
        assert t.hbar.winfo_manager() == "pack"
        t._xview("moveto", 0.5)
        t.update()
        assert t.body.xview()[0] > 0.2
        assert t.header.xview()[0] == pytest.approx(t.body.xview()[0], abs=0.01)
        t.reset_layout()
        t.draw()
        t.update()
        assert t.hbar.winfo_manager() == ""
    finally:
        ui.root.withdraw()


# ---- resize -------------------------------------------------------------------------------------

def test_dragging_a_border_resizes_that_column_without_sorting(ui):
    t = fresh(ui)
    try:
        sorts = []
        t._on_sort = sorts.append
        before = t._layout()[0][t.order.index("title")]
        x = border(t, "title")
        assert t._head_hit(type("E", (), {"x": x + 2, "y": 5})) == ("border", t.order.index("title"))
        t.header.event_generate("<Motion>", x=x, y=5)
        assert str(t.header.cget("cursor")) == "sb_h_double_arrow"
        t.header.event_generate("<Motion>", x=centre(t, "title"), y=5)
        assert str(t.header.cget("cursor")) == ""
        drag(t, x, x + 40)
        after = t._layout()[0][t.order.index("title")]
        assert after == before + 40 and sorts == []
        drag(t, border(t, "title"), border(t, "title") - 2000)      # far left: minimum width
        assert t._layout()[0][t.order.index("title")] == t.min_width("title")
        assert sorts == []
        drag(t, border(t, "title"), border(t, "title") + 5000)      # far right: capped
        assert t._layout()[0][t.order.index("title")] == int(round(700 * t.scale))
        assert ui_json(ui)["columns"]["widths"]["title"] == 700
    finally:
        ui.root.withdraw()


def test_a_click_on_a_border_without_moving_changes_nothing_and_does_not_save(ui):
    t = fresh(ui)
    try:
        path = ui.local_dir / "ui.json"
        path.unlink(missing_ok=True)
        x = border(t, "genre")
        press(t, x)
        release(t, x)
        assert t.layout_state()["widths"] == {} and not path.exists()
    finally:
        ui.root.withdraw()


def test_double_click_on_a_border_fits_the_column_to_its_content(ui):
    t = fresh(ui)
    try:
        sorts = []
        t._on_sort = sorts.append
        key = "sessions"
        longest = max(t._row_style(r, key, r.cells[t.keys.index(key)])[0].measure(
            r.cells[t.keys.index(key)].text) for r in t._rows)
        x = border(t, key)
        dblclick(t, x)
        t.update()
        t.draw()
        w = t._layout()[0][t.order.index(key)]
        assert w == longest + 2 * t.pad + 2 and sorts == []
        # tiny content column: shrinks to the header (or minimum); big one is capped
        dblclick(t, border(t, "age"))
        assert t._layout()[0][t.order.index("age")] >= t.min_width("age")
        t.insert("", "end", iid="huge", values=["x" * 3000] * len(t.keys))
        t.autofit("title")
        assert t._user_w["title"] == int(round(700 * t.scale))
    finally:
        ui.root.withdraw()


# ---- reorder ------------------------------------------------------------------------------------

def test_dragging_a_header_reorders_and_keeps_hit_testing_and_clicks_correct(ui):
    t = fresh(ui)
    try:
        sorts, cells = [], []
        t._on_sort, t._on_cell = sorts.append, lambda iid, key: cells.append((iid, key))
        start = list(t.order)
        press(t, centre(t, "price"))
        move(t, centre(t, "price") - 30)
        move(t, border(t, "title") - 30)         # towards the boundary before "title"
        assert t.header.find_withtag("ovl") and t.body.find_withtag("ovl")   # ghost + drop lines
        release(t, border(t, "follow") + 3)      # drop right after Takip
        t.draw()
        assert t.order == ["follow", "price"] + [k for k in start if k not in ("follow", "price")]
        assert sorts == [] and not t.header.find_withtag("ovl")
        assert ui_json(ui)["columns"]["order"][:2] == ["follow", "price"]
        assert t.keys == start                      # data order untouched
        # Takip is now column 0 still; move it to the end and click its checkbox there
        drag(t, centre(t, "follow"), xs_of(t)[-1] - 4)
        assert t.order[-1] == "follow"
        first = t.get_children()[0]
        y = t.row_px // 2
        ev = type("E", (), {"x": centre(t, "follow"), "y": y})
        assert t._hit(ev) == (first, "follow")
        t._click(ev)
        ev2 = type("E", (), {"x": centre(t, "title"), "y": y + t.row_px})
        assert t._hit(ev2) == (t.get_children()[1], "title")
        assert cells == [(first, "follow")] and t.selection() == ()
        t._click(ev2)
        assert t.selection() == (t.get_children()[1],)
        # link column too
        ev3 = type("E", (), {"x": centre(t, "link"), "y": y})
        t._click(ev3)
        assert cells[-1] == (first, "link")
        # a plain click (no drag) sorts the column under the cursor, in the current order
        press(t, centre(t, "age"))
        release(t, centre(t, "age"))
        assert sorts == ["age"]
        # a tiny wobble is still a click
        press(t, centre(t, "age"))
        move(t, centre(t, "age") + 2)
        release(t, centre(t, "age") + 2)
        assert sorts == ["age", "age"]
    finally:
        ui.root.withdraw()


def test_reorder_keeps_styling_and_cell_values_with_their_headers(ui):
    t = fresh(ui)
    try:
        first = t.get_children()[0]
        texts = {k: t.cell(first, k).text for k in t.keys}
        t.move_column("zaman", 0)
        t.move_column("title", len(t.order))
        t.draw()
        assert t.order[0] == "zaman" and t.order[-1] == "title"
        head = [(t.header.itemcget(i, "text")) for i in t.header.find_all()
                if t.header.type(i) == "text"]
        assert head[0] == "Zaman" and head[-1] == "Oyun"
        assert {k: t.cell(first, k).text for k in t.keys} == texts
        body = [t.body.itemcget(i, "text") for i in t.body.find_all()
                if t.body.type(i) == "text"][:len(t.order)]
        assert body[0] == texts["zaman"] or body[0].startswith(texts["zaman"][:3])
        # zebra background still drawn on odd rows
        assert any(t.body.itemcget(i, "fill") == "#f7f8fa" for i in t.body.find_all()
                   if t.body.type(i) == "rectangle")
    finally:
        ui.root.withdraw()


def test_reorder_in_a_table_without_customize_is_off(tk_root):
    frame = tk.Frame(tk_root)
    t = __import__("pc.table", fromlist=["GridTable"]).GridTable(
        frame, (("a", "A", 80), ("b", "B", 120)), sortable=True, on_sort=lambda c: None)
    t.pack(fill="both", expand=True)
    frame.pack(fill="both", expand=True)
    tk_root.deiconify()
    tk_root.geometry("400x200+0+0")
    tk_root.update()
    try:
        press(t, 20)
        move(t, 150)
        release(t, 150)
        assert t.order == ["a", "b"] and t._head_hit(type("E", (), {"x": 80, "y": 3}))[0] == "cell"
    finally:
        frame.destroy()
        tk_root.withdraw()


# ---- persistence ----------------------------------------------------------------------------------

def _restart(ui, tmp_path):
    ui.save_ui()
    root = ui.root
    ui.closing = True
    for w in list(root.winfo_children()):
        w.destroy()
    root.configure(menu=tk.Menu(root))
    svc = FakeService()
    app = appmod.App(root, svc, tmp_path, open_url=lambda u: None, beep=lambda: None,
                     msgbox=Box(), inline=True)
    app.svc = svc
    _many_plays(app)
    app.update_status()
    _show(app, 1760, 1015)
    app.tree.draw()
    return app


def test_widths_order_and_hidden_survive_a_restart(ui, tmp_path):
    t = fresh(ui)
    app = None
    try:
        drag(t, border(t, "title"), border(t, "title") + 33)
        drag(t, centre(t, "zaman"), centre(t, "title") - 10)
        t.toggle_column("age")
        want = t.layout_state()
        assert "age" in want["hidden"] and want["order"].index("zaman") < want["order"].index("title")
        assert ui_json(ui)["columns"] == want
        app = _restart(ui, tmp_path)
        assert app.tree.layout_state() == want
        assert "age" not in app.tree.order and app.tree._user_w["title"] == t._user_w["title"]
        assert app.tree.order.index("zaman") < app.tree.order.index("title")
        # file also still holds the other settings written by save_ui
        data = ui_json(app)
        assert "geometry" in data and "beep" in data and "seen" in data
    finally:
        for a in (app, ui):
            if a is not None:
                a.closing = True
        ui.root.withdraw()


def test_unknown_missing_and_garbage_saved_columns_are_tolerated(tk_root, tmp_path):
    saved = {"columns": {"order": ["rez", "gone", "title", "title", 7],
                         "hidden": ["nothere", "age"],
                         "widths": {"title": 333, "gone": 50, "rez": "x", "age": 5, "price": 99999}}}
    (tmp_path / "ui.json").write_text(json.dumps(saved), encoding="utf-8")
    for w in tk_root.winfo_children():
        w.destroy()
    tk_root.configure(menu=tk.Menu(tk_root))
    app = appmod.App(tk_root, FakeService(), tmp_path, open_url=lambda u: None, msgbox=Box(),
                     inline=True)
    try:
        t = app.tree
        # saved: rez, title; every other column is missing -> inserted at its default place
        assert sorted(t._order) == sorted(KEYS)
        assert t._order.index("rez") < t._order.index("title")
        assert t._order.index("follow") == t._order.index("rez") - 1 or t._order[0] == "follow"
        assert t._hidden == {"age"} and t._user_w == {"title": 333}
        for bad in ("garbage", [], 7, {"order": 5, "hidden": None, "widths": []}):
            t.set_layout_state(bad)
            assert t.order == KEYS
    finally:
        app.closing = True
        for w in list(tk_root.winfo_children()):
            w.destroy()


def test_corrupt_ui_json_columns_fall_back_to_default(tk_root, tmp_path):
    (tmp_path / "ui.json").write_text('{"columns": 12}', encoding="utf-8")
    for w in tk_root.winfo_children():
        w.destroy()
    tk_root.configure(menu=tk.Menu(tk_root))
    app = appmod.App(tk_root, FakeService(), tmp_path, open_url=lambda u: None, msgbox=Box(),
                     inline=True)
    try:
        assert app.tree.order == KEYS
    finally:
        app.closing = True
        for w in list(tk_root.winfo_children()):
            w.destroy()


# ---- header menu ----------------------------------------------------------------------------------

def test_header_menu_resets_and_shows_hides_columns_but_keeps_one(ui):
    t = fresh(ui)
    try:
        t.move_column("price", 0)
        t._user_w["title"] = 400
        menu = t.header_menu()
        assert menu.entrycget(0, "label") == RESET_LABEL == "Sütunları varsayılana sıfırla"
        labels = [menu.entrycget(i, "label") for i in range(2, menu.index("end") + 1)]
        assert labels[0] == "Fiyat" and len(labels) == len(KEYS)
        idx = 2 + labels.index("Süre")
        menu.invoke(idx)                                   # hide Süre
        assert "duration" not in t.order
        assert "duration" in ui_json(ui)["columns"]["hidden"]
        menu = t.header_menu()
        assert menu.entrycget(idx, "label") == "Süre"
        menu.invoke(idx)                                   # show again
        assert "duration" in t.order
        # hide everything except one
        for key in list(t.order)[:-1]:
            assert t.toggle_column(key)
        assert len(t.order) == 1 and not t.toggle_column(t.order[0])
        menu = t.header_menu()
        last = [menu.entrycget(i, "state") for i in range(2, menu.index("end") + 1)]
        assert last.count("disabled") == 1
        t.draw()
        t.update()
        assert t._layout()[1][-1] > 0 and t._layout()[0] == [t.body.winfo_width()]
        t.header_menu().invoke(0)                          # reset
        assert t.order == KEYS and t._hidden == set() and t._user_w == {}
        assert ui_json(ui)["columns"] == {"order": KEYS, "hidden": [], "widths": {}}
    finally:
        ui.root.withdraw()


def test_hiding_a_column_refits_the_rest_and_keeps_rows_aligned(ui):
    t = fresh(ui)
    try:
        t.toggle_column("sessions")
        t.draw()
        t.update()
        widths, xs = t._layout()
        assert "sessions" not in t.order and xs[-1] <= t.body.winfo_width()
        ev = type("E", (), {"x": centre(t, "summary"), "y": 3})
        assert t._hit(ev) == (t.get_children()[0], "summary")
    finally:
        ui.root.withdraw()


# ---- real window at both sizes ----------------------------------------------------------------------

@pytest.mark.parametrize("width,height", [(1760, 1015), (1000, 650)])
def test_window_flow_at_both_sizes_resize_reorder_sort_toggle(ui, width, height):
    t = fresh(ui, width, height)
    try:
        drag(t, border(t, "source"), border(t, "source") + 25)
        drag(t, centre(t, "follow"), centre(t, "price"))
        assert t.order.index("follow") > t.order.index("title")
        # sort through the real header click still goes through the window
        press(t, centre(t, "title"))
        release(t, centre(t, "title"))
        assert ui.sort == ("title", "asc")
        first = t.get_children()[0]
        click = type("E", (), {"x": centre(t, "follow"), "y": t.row_px // 2})
        assert t._hit(click) == (first, "follow")
        calls = []
        orig = ui._cell_click
        t._on_cell = lambda iid, key: (calls.append((iid, key)), orig(iid, key))
        t._click(click)
        assert calls == [(first, "follow")]
        for _ in range(50):
            ui.root.update()
        toggled = [a for name, a in ui.svc.calls if name == "set_active"]
        assert toggled and toggled[-1][0] == first            # the row under the pointer
        link = type("E", (), {"x": centre(t, "link"), "y": t.row_px // 2 + t.row_px})
        t._click(link)
        second = t.get_children()[1]
        assert ui.opened and ui.opened[-1] == ui.rows_by_slug[second].url
        _w, xs = t._layout()
        if width >= 1500:
            assert xs[-1] <= t.body.winfo_width() + 650      # user width added, fit not broken
    finally:
        ui.root.withdraw()
