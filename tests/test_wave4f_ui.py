"""Wave 4 F / wave 5: window sizing, proportional panes, auto-hide scrollbars."""
from __future__ import annotations

import pytest

from pc import app_logic as L

tk = pytest.importorskip("tkinter")
from tkinter import ttk  # noqa: E402

from pc.widgets import AutoScrollbar  # noqa: E402
from tests.test_pc_app_smoke import tk_root, ui  # noqa: E402,F401  (fixtures)


# ---- pure ---------------------------------------------------------------------------------

def test_default_geometry_is_92_percent_of_the_screen_centred():
    w, h, x, y = (int(v) for v in re_split(L.default_geometry(1920, 1080)))
    assert (w, h) == (int(1920 * 0.92), int(1080 * 0.92)) and x == (1920 - w) // 2
    assert 0 <= y <= 1080 - h
    w, h = (int(v) for v in re_split(L.default_geometry(2560, 1440))[:2])
    assert (w, h) == (int(2560 * 0.92), int(1440 * 0.92))
    # small screens: never below the default minimum unless the screen itself is smaller
    w, h = (int(v) for v in re_split(L.default_geometry(1366, 768))[:2])
    assert w == int(1366 * 0.92) and h == max(650, int(768 * 0.92))
    assert re_split(L.default_geometry(800, 500))[:2] == ["800", "500"]
    assert L.sanitize_geometry("1280x820+10+10", 1920, 1080) == "1280x820+10+10"


def re_split(geometry):
    import re
    return re.split(r"[x+]", geometry)


def test_old_saved_geometry_is_discarded_once_then_kept(tmp_path):
    import json
    p = tmp_path / "ui.json"
    p.write_text(json.dumps({"geometry": "700x500+10+10", "beep": True}), encoding="utf-8")
    assert L.load_ui(p)["geometry"] is None                       # saved before the version
    p.write_text(json.dumps({"geometry": "700x500+10+10", "beep": True, "geometry_v": 1}),
                 encoding="utf-8")
    assert L.load_ui(p)["geometry"] is None                       # an older version
    p.write_text(json.dumps({"geometry": "700x500+10+10", "beep": True,
                             "geometry_v": L.GEOMETRY_VERSION}), encoding="utf-8")
    assert L.load_ui(p)["geometry"] == "700x500+10+10"


def test_list_pane_fits_its_rows_and_leaves_the_detail_what_it_needs():
    pane = 1000
    floor3 = L.HEADING_PX + 3 * L.ROW_PX + 6
    assert L.list_pane_px(2, pane) == L.list_pane_px(3, pane) == floor3   # few rows: no waste
    few, mid, many = (L.list_pane_px(n, pane) for n in (2, 20, 200))
    assert mid == L.HEADING_PX + 20 * L.ROW_PX + 6 and few < mid < many
    assert many == int(pane * L.LIST_SHARE)          # unknown need: the list keeps its share
    # wave 12: the list keeps at least 55 % even when the detail wants more (it scrolls) ...
    assert L.list_pane_px(200, pane, 700) == int(pane * 0.55)
    assert L.list_pane_px(200, pane, 950) == int(pane * 0.55)
    # ... and takes everything the detail does not need
    assert L.list_pane_px(200, pane, 300) == 700
    assert L.list_pane_px(5, pane, 300) == L.HEADING_PX + 5 * L.ROW_PX + 6   # list needs less
    assert L.list_pane_px(6, 100) <= 100


# ---- auto-hide scrollbar -----------------------------------------------------------------------

def test_scrollbar_hides_when_everything_fits_and_returns_when_it_does_not(tk_root):
    frame = ttk.Frame(tk_root)
    sb = AutoScrollbar(frame, orient="vertical")
    sb.pack(side="right", fill="y")
    assert sb.winfo_manager() == "pack"
    sb.set(0.0, 1.0)
    assert sb.winfo_manager() == ""
    sb.set(0.0, 1.0)                                  # idempotent
    sb.set(0.0, 0.4)
    assert sb.winfo_manager() == "pack" and sb.shown
    sb.set(0.3, 1.0)
    assert sb.winfo_manager() == "pack"
    sb.set("0", "1")                                  # Tk passes strings
    assert sb.winfo_manager() == ""
    frame.destroy()


def test_main_list_scrollbar_follows_the_content(ui):
    ui.root.update()
    ui.tree.event_generate("<Configure>")
    ui.root.update()
    # two plays fit in the list: no scrollbar
    kids = ui.tree.get_children()
    assert len(kids) == 2
    ui.tree.configure(yscrollcommand=lambda a, b: None)
    top = ui.tree.master
    bars = [w for w in top.winfo_children() if isinstance(w, AutoScrollbar)]
    assert len(bars) == 1
    bars[0].set(0.0, 1.0)
    assert bars[0].winfo_manager() == ""


def test_window_is_not_smaller_than_the_default_unless_the_screen_is(ui):
    ui.root.update()
    sw, sh = ui.root.winfo_screenwidth(), ui.root.winfo_screenheight()
    w, h = (int(x) for x in re_split(L.default_geometry(sw, sh))[:2])
    assert ui.root.winfo_width() >= min(w, sw) - 5 or ui.root.winfo_width() >= 1000


def test_panes_are_sized_to_the_row_count(ui):
    ui.root.update()
    ui._sash_user, ui._fit_key = False, None
    ui._fit_panes()
    ui.root.update()
    pane_px = ui.pane.winfo_height()
    if pane_px >= 100:
        assert ui.pane.sashpos(0) == L.list_pane_px(
            len(ui.rows), pane_px, ui._placeholder_px(pane_px))
        # the user drags the divider: later refreshes leave it alone
        ui._sash_user = True
        ui.pane.sashpos(0, 150)
        ui._fit_key = None
        ui._fit_panes()
        assert ui.pane.sashpos(0) == 150
