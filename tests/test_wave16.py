"""Wave 16: source-event (Başiskele / VKV) detail pane: facts block in the right column next
to Özet, layout computed once per selection, one column when narrow, nothing dropped."""
from __future__ import annotations

import time

import pytest

tk = pytest.importorskip("tkinter")
from tests.test_pc_app_smoke import Box, FakeService, tk_root, ui  # noqa: E402,F401
from tests.test_wave8 import _show  # noqa: E402,F401
from tests.test_wave14 import Recorder  # noqa: E402
from tests.test_wave15_info import _events_in_ui  # noqa: E402


def _rec_select(ui, slug, seconds=0.8):
    rec = Recorder(ui)
    ui.select_slug(slug)
    end = time.time() + seconds
    while time.time() < end:
        ui.root.update()
        time.sleep(0.004)
    ui.root.update()
    ui.root.unbind_all("<Configure>")
    return rec


def _rects(ui):
    w = ui.event_info_labels
    return {"ozet": w["Özet"], "oyuncular": w["Oyuncular"], "yapim": w["Yapım"],
            "facts": ui.event_facts_grid, "help": ui.follow_help}


def _rel(widget, anchor):
    return (widget.winfo_rootx() - anchor.winfo_rootx(), widget.winfo_rooty() - anchor.winfo_rooty())


def test_wide_facts_in_right_column_next_to_ozet(ui):
    _events_in_ui(ui, with_cast=True)
    _show(ui, 1760, 1015)
    ui.select_slug("vkv:541")
    for _ in range(5):
        ui.root.update()
    r = _rects(ui)
    body = ui.detail_body
    fx, fy = _rel(r["facts"], body)
    ox, oy = _rel(r["ozet"], body)
    hx, hy = _rel(r["help"], body)
    px_, py_ = _rel(r["oyuncular"], body)
    assert fx > ox + 200, "facts must sit right of Özet"
    assert fx > hx + 200 and fy < oy, "facts at the top, beside the header block"
    assert abs(px_ - fx) <= 30, "Oyuncular/Yapım in the right column too"
    assert oy < fy + r["facts"].winfo_height() + 400
    # Özet and the facts are visible together, without scrolling
    canvas_h = ui.detail_canvas.winfo_height()
    assert oy + r["ozet"].winfo_height() <= canvas_h or oy < canvas_h
    # every fact stays shown
    texts = [w.cget("text") for w in ui.event_facts_grid.winfo_children()]
    assert "Kaynak" in texts and "Etkinlik tarihi" in texts and "Rezervasyon açılışı" in texts
    assert "Durum" in texts and "Mekan" in texts
    assert all(int(l.cget("wraplength")) != 700 for l in ui.event_fact_labels)


def test_narrow_stacks_in_one_column(ui):
    _events_in_ui(ui, with_cast=True)
    _show(ui, 1010, 900)
    ui.select_slug("vkv:541")
    for _ in range(5):
        ui.root.update()
    r = _rects(ui)
    body = ui.detail_body
    xs = {_rel(r[n], body)[0] for n in ("facts", "ozet", "oyuncular")}
    assert max(xs) - min(xs) <= 24, xs
    assert _rel(r["facts"], body)[1] > _rel(r["help"], body)[1]


@pytest.mark.parametrize("width", [1760, 1010])
def test_event_selection_lays_out_once(ui, width):
    _events_in_ui(ui, with_cast=True)
    _show(ui, width, 1015)
    ui.select_slug("vkv:540")
    Recorder(ui).run(0.4)
    ui.root.unbind_all("<Configure>")
    rec = _rec_select(ui, "vkv:541")
    for name, w in _rects(ui).items():
        r = rec.rects(w)
        assert len(r) <= 2, f"{width}: {name} geometry changed {len(r)} times: {r}"
    s = rec.sash_changes()
    assert len(s) <= 2, f"divider moved {s}"
    rec2 = Recorder(ui)
    rec2.run(0.4)
    ui.root.unbind_all("<Configure>")
    names = {str(w) for w in _rects(ui).values()}
    assert [e for e in rec2.events if e[0] in names] == []


def test_no_fixed_700px_wrap_and_rewraps_on_resize(ui):
    _events_in_ui(ui, with_cast=True)
    _show(ui, 1760, 1015)
    ui.select_slug("vkv:541")
    for _ in range(5):
        ui.root.update()
    wide = ui.event_info_labels["Özet"].winfo_width()      # the Özet box takes its column width
    _show(ui, 1010, 1015)
    for _ in range(5):
        ui.root.update()
    narrow = ui.event_info_labels["Özet"].winfo_width()
    assert wide not in (700,) and narrow not in (700,)
    assert wide != narrow
