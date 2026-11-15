"""Wave 12: left sidebar for the fixed parts, tabs + big list + two-column detail on the right,
cleaned Özet, narrow-window fallback, DPI scaling."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from pc import app_logic as L
from pc import ui as appmod
from tiyatro.models import ISTANBUL
from tiyatro.tags import clean_summary

tk = pytest.importorskip("tkinter")
from tests.test_pc_app_smoke import Box, FakeService, tk_root, ui  # noqa: E402,F401
from tests.test_wave8 import FakeWin, _show, _three_sources, inside, rect  # noqa: E402,F401
from tests.test_wave9 import _many_plays, _overlap  # noqa: E402

RAW_SUMMARY = ("Ilk paragraf burada.\r\n \r\n \r\n\r\n\r\nIkinci paragraf.\r\n\n\n  \n"
               "Ucuncu satir.\r\n\r\n")
CLEAN_SUMMARY = "Ilk paragraf burada.\n\nIkinci paragraf.\n\nUcuncu satir."


# ---- Özet cleanup (shared function) -------------------------------------------------------------

def test_clean_summary_collapses_blank_runs_and_odd_whitespace():
    raw = "\r\n\r\n  Ilk satir  \r\n \r\n \r\n\t\r\n\r\nIkinci satir\rUcuncu\n\n\n\n  \n"
    assert clean_summary(raw) == "Ilk satir\n\nIkinci satir\nUcuncu"
    assert clean_summary("a\n\nb") == "a\n\nb"           # one blank line is kept
    assert clean_summary("   \n   \n") == ""
    assert clean_summary(None) == "" and clean_summary(5) == ""
    assert clean_summary("tek​satir") == "teksatir"
    assert clean_summary(RAW_SUMMARY) == CLEAN_SUMMARY


def test_viewmodel_row_summary_is_clean_but_stored_state_is_untouched(ui):
    ui.svc._state["plays"]["p1"]["summary"] = RAW_SUMMARY
    ui.refresh_rows()
    row = next(r for r in ui.rows if r.slug == "p1")
    assert row.summary == CLEAN_SUMMARY
    assert ui.svc._state["plays"]["p1"]["summary"] == RAW_SUMMARY     # display only


def test_summary_text_widget_shows_cleaned_text_sized_to_it(ui):
    ui.svc._state["plays"]["p1"]["summary"] = RAW_SUMMARY
    ui.refresh_rows()
    ui.select_slug("p1")
    shown = ui.summary_text.get("1.0", "end-1c")
    assert shown == CLEAN_SUMMARY
    assert "\n\n\n" not in shown and not shown.startswith("\n") and not shown.endswith("\n")
    lines = int(ui.summary_text.cget("height"))
    assert lines >= len(CLEAN_SUMMARY.split("\n"))            # fits the content ...
    assert lines <= len(CLEAN_SUMMARY.split("\n")) + 2        # ... and no empty rows below it


def test_a_whitespace_only_summary_shows_bilgi_yok(ui):
    ui.svc._state["plays"]["p1"]["summary"] = " \r\n \r\n "
    ui.refresh_rows()
    ui.select_slug("p1")
    assert ui.summary_text.get("1.0", "end-1c") == "Bilgi yok"


# ---- layout: sidebar left, tabs right ---------------------------------------------------------

def test_sidebar_holds_status_buttons_cards_and_legend_and_tabs_are_on_the_right(ui):
    _many_plays(ui)
    _show(ui, 1760, 1015)
    try:
        side, nb = rect(ui.side_wrap), rect(ui.notebook)
        assert not ui.narrow
        assert side[2] - side[0] == ui.px(appmod.SIDE_W)             # fixed width
        assert side[2] <= nb[0] + 1 and side[3] - side[1] >= 1000     # left of the tabs, full height
        assert nb[2] - nb[0] >= 1760 - ui.px(appmod.SIDE_W) - 20      # tabs use the rest
        assert [ui.notebook.tab(t, "text") for t in ui.notebook.tabs()] == [
            "Oyunlar", "Etkinlik günlüğü"]
        for block in (ui.status, ui.source_bar, ui.legend):
            assert inside(rect(block), rect(ui.side_canvas))
        blocks = [rect(ui.status), rect(ui.source_bar), rect(ui.legend)]
        for a, b in zip(blocks, blocks[1:]):
            assert a[3] <= b[1] + 1
        for key, lab in ui.status_labels.items():                     # compact key/value block
            assert inside(rect(lab), rect(ui.status)), key
        ys = [lab.winfo_rooty() for lab in ui.status_labels.values()]
        assert ys == sorted(ys) and len(set(ys)) == 6
        stack = [ui.sidebar]                                          # nothing clipped
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            if w.winfo_class() in ("Label", "TButton") and w.winfo_ismapped():
                assert w.winfo_width() >= w.winfo_reqwidth() - 1, w.cget("text")
                assert w.winfo_height() >= w.winfo_reqheight() - 1, w.cget("text")
        assert ui.side_vsb.shown is False       # fits at this height: no scrollbar
    finally:
        ui.root.withdraw()


def test_list_gets_most_of_the_main_area_and_shows_many_rows(ui):
    _many_plays(ui)
    for width, height, rows, share in ((1760, 1015, 22, 0.55), (1500, 850, 15, 0.55),
                                       (1000, 650, 11, 0.5)):
        _show(ui, width, height)
        try:
            assert ui.tree.winfo_height() >= share * ui.pane.winfo_height() - 8, (width, height)
            assert ui.tree.body.winfo_height() // ui.tree.row_px >= rows, (width, height)
            assert inside(rect(ui.pane), rect(ui.notebook))
            assert not _overlap(rect(ui.side_wrap), rect(ui.notebook))
        finally:
            ui.root.withdraw()


def test_sidebar_scrolls_by_itself_only_when_the_window_is_too_low(ui):
    _many_plays(ui)
    _show(ui, 1000, 650)
    try:
        assert ui.side_vsb.shown is True
        assert ui.side_canvas.yview()[1] < 1.0
        ui.side_canvas.yview_moveto(1.0)
        ui.root.update()
        assert ui.side_canvas.yview()[1] == pytest.approx(1.0)
        assert rect(ui.legend)[3] <= rect(ui.side_canvas)[3] + 1      # last block reachable
    finally:
        ui.root.withdraw()


def test_narrow_window_is_a_top_strip_and_wide_again_returns_to_the_sidebar(ui):
    _many_plays(ui)
    _show(ui, 800, 800)
    try:
        assert ui.narrow is True
        strip, nb = rect(ui.side_wrap), rect(ui.notebook)
        assert strip[3] <= nb[1] + 1 and strip[2] - strip[0] >= 780
        assert strip[3] - strip[1] <= 0.42 * 800 + 2                   # capped, then it scrolls
        assert rect(ui.status)[2] <= rect(ui.source_bar)[0] + 1          # status | cards side by side
        _show(ui, 1300, 800)
        assert ui.narrow is False
        assert rect(ui.side_wrap)[2] <= rect(ui.notebook)[0] + 1
    finally:
        ui.root.withdraw()


# ---- layout: detail pane --------------------------------------------------------------------

def _detail_ui(ui):
    now = datetime.now(ISTANBUL)
    ui.svc._state["plays"]["p1"].update(
        summary=("Bu bir konu özetidir. " * 20) + "\r\n\r\n\r\n" + ("Ikinci paragraf. " * 10),
        cast=["Ali Can", "Ece Er (Hamlet)", "Veli Yol"],
        credits={"Yazan": "A B", "Yönetmen": "C D"})
    for k in range(5):
        ui.svc._state["plays"]["p1"]["sessions"][f"x{k}"] = {
            "start": (now + timedelta(days=3 + k)).isoformat(), "status": "on_sale"}
    _many_plays(ui)
    ui.select_slug("p1")


def test_detail_uses_two_columns_when_wide_without_gaps_or_clipping(ui):
    _detail_ui(ui)
    _show(ui, 1500, 850)
    try:
        assert ui._detail_stack is False
        left, targets, summary, people = (rect(ui._detail_parts[n]) for n in
                                          ("left", "targets", "summary", "people"))
        assert left[2] <= targets[0] and summary[2] <= people[0]          # side by side
        assert left[3] <= summary[1] + 1 and targets[3] <= people[1] + 1  # Özet under the table
        assert summary[2] - summary[0] > people[2] - people[0]            # Özet is the wide one
        assert left[0] == summary[0] and targets[0] == people[0]          # aligned columns
        body = rect(ui.detail_body)
        for part in (left, targets, summary, people):
            assert inside(part, body)
        for a, b in ((left, targets), (summary, people), (left, summary), (targets, people)):
            assert not _overlap(a, b)
        assert inside(rect(ui.session_tree), left) and inside(rect(ui.active_chk), left)
        assert inside(rect(ui.cast_label), people) and inside(rect(ui.credits_label), people)
        fits = ui.detail_body.winfo_reqheight() <= ui.detail_canvas.winfo_height()
        assert ui.detail_vsb.shown is (not fits)           # scrollbar only when it does not fit
        if fits:
            assert ui.detail_canvas.winfo_height() - ui.detail_body.winfo_height() < ui.px(60)
        for lab in (ui.cast_label, ui.credits_label):
            assert lab.winfo_width() >= lab.winfo_reqwidth() - 1
    finally:
        ui.root.withdraw()


def test_detail_stacks_its_blocks_when_narrow_and_scrolls_instead_of_clipping(ui):
    _detail_ui(ui)
    _show(ui, 1000, 650)
    try:
        assert ui._detail_stack is True
        order = [rect(ui._detail_parts[n]) for n in ("left", "targets", "summary", "people")]
        for a, b in zip(order, order[1:]):
            assert a[3] <= b[1] + 1 and not _overlap(a, b)
        assert abs(order[0][0] - order[3][0]) <= 1                       # one column
        if ui.detail_body.winfo_reqheight() > ui.detail_canvas.winfo_height():
            assert ui.detail_vsb.shown is True                            # scrollbar, not clipping
    finally:
        ui.root.withdraw()


# ---- DPI --------------------------------------------------------------------------------------

def test_sidebar_and_strip_threshold_scale_with_dpi(tk_root, tmp_path):
    old = float(tk_root.tk.call("tk", "scaling"))
    for w in tk_root.winfo_children():
        w.destroy()
    tk_root.tk.call("tk", "scaling", 2.0)           # 144 dpi
    app = None
    try:
        app = appmod.App(tk_root, FakeService(), tmp_path, open_url=lambda u: None,
                         msgbox=Box(), inline=True, win=FakeWin())
        app.svc = app.service
        _many_plays(app)
        app.update_status()
        app.select_slug("p1")
        _show(app, 2200, 1300)
        assert app.scale == pytest.approx(1.5, rel=0.01)
        side = rect(app.side_wrap)
        assert side[2] - side[0] == app.px(appmod.SIDE_W) in range(418, 423)
        assert not app.narrow                       # 2200 px > 1000 * 1.5
        assert not _overlap(side, rect(app.notebook))
        assert inside(rect(app.status), rect(app.side_canvas))
        _show(app, 1400, 900)                       # 1400 < 1500 (= 1000 dp): strip on top
        assert app.narrow
    finally:
        if app is not None:
            app.closing = True
        for w in list(tk_root.winfo_children()):
            w.destroy()
        tk_root.tk.call("tk", "scaling", old)
        tk_root.withdraw()


def test_list_pane_logic_keeps_the_share_and_shrinks_to_few_rows():
    pane = 900
    assert L.list_pane_px(100, pane, 200) == pane - 200         # detail needs little: list takes the rest
    assert L.list_pane_px(100, pane, 700) == int(pane * L.LIST_SHARE)   # detail needs a lot: 55 %
    assert L.list_pane_px(4, pane, 700) == L.HEADING_PX + 4 * L.ROW_PX + 6   # few rows: just those
