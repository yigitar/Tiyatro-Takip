"""Wave 4 F: window sizing, proportional panes, auto-hide scrollbars, "Diğer kaynaklar" tab."""
from __future__ import annotations

import pytest

from pc import app_logic as L

tk = pytest.importorskip("tkinter")
from tkinter import ttk  # noqa: E402

from pc.events_ui import COLUMNS, FOLLOW_OFF, FOLLOW_ON, row_values  # noqa: E402
from pc.widgets import AutoScrollbar  # noqa: E402
from tests.test_pc_app_smoke import tk_root, ui  # noqa: E402,F401  (fixtures)


# ---- pure ---------------------------------------------------------------------------------

def test_default_geometry_is_large_but_clamped_to_the_screen():
    assert L.default_geometry(2560, 1440) == "1280x820"
    assert L.default_geometry(1920, 1080) == "1280x820"
    assert L.default_geometry(1366, 768) == "1280x668"
    assert L.default_geometry(1024, 600) == "984x500"
    assert L.default_geometry(300, 200) == "400x300"      # never below the minimum
    assert L.sanitize_geometry("1280x820+10+10", 1920, 1080) == "1280x820+10+10"


def test_list_pane_fits_its_rows_and_never_takes_the_whole_pane():
    pane = 1000
    few = L.list_pane_px(2, pane)
    mid = L.list_pane_px(20, pane)
    many = L.list_pane_px(200, pane)
    assert few == L.list_pane_px(0, pane) == L.list_pane_px(3, pane) == 350   # >= 35 %
    assert mid == L.HEADING_PX + 20 * L.ROW_PX + 6 and few < mid < many
    assert many == int(pane * 0.6)                                            # detail keeps >= 40 %
    assert L.list_pane_px(6, 100) <= 60


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
    w, h = (int(x) for x in L.default_geometry(sw, sh).split("x"))
    assert ui.root.winfo_width() >= min(w, sw) - 5 or ui.root.winfo_width() >= 1000


def test_panes_are_sized_to_the_row_count(ui):
    ui.root.update()
    ui._sash_user, ui._fit_key = False, None
    ui._fit_panes()
    ui.root.update()
    pane_px = ui.pane.winfo_height()
    if pane_px >= 100:
        assert ui.pane.sashpos(0) == L.list_pane_px(len(ui.rows), pane_px)
        # the user drags the divider: later refreshes leave it alone
        ui._sash_user = True
        ui.pane.sashpos(0, 150)
        ui._fit_key = None
        ui._fit_panes()
        assert ui.pane.sashpos(0) == 150


# ---- Diğer kaynaklar tab ------------------------------------------------------------------------

def view(eid="vkv:541", followed=True, full=False, source="vkv", title="Ceviz Adam Müzikali"):
    return {"id": eid, "source": source, "source_name": "VKV Gölcük" if source == "vkv"
            else "Başiskele Belediyesi", "title": title, "when": "28.11.2026 11:00",
            "audience": "Çocuk", "genre": "Müzikal", "kind": "Çocuk Tiyatrosu", "venue": "V",
            "url": "https://example.test/" + eid, "reservation": "Doldu" if full
            else "19 Kasım 12:00'da açılacak", "full": full, "followed": followed, "start": None}


@pytest.fixture
def tab(ui):
    ui.views = [view(), view("bk:1", True, False, "basiskele", "Çöp Atlas"),
                view("vkv:9", False, True, title="Kapalı Oyun")]
    toggles = []
    ui.svc.event_views = lambda now=None: list(ui.views)

    def set_follow(eid, on):
        toggles.append((eid, on))
        for v in ui.views:
            if v["id"] == eid:
                v["followed"] = on
    ui.svc.set_event_follow = set_follow
    ui.toggles = toggles
    ui.refresh_rows()
    return ui.events_tab


def test_tab_exists_between_the_other_tabs(ui):
    nb = ui.notebook
    assert [nb.tab(t, "text") for t in nb.tabs()][1] == "Diğer kaynaklar"


def test_tab_columns_and_rows(tab):
    assert [tab.tree.heading(c)["text"] for c in tab.tree["columns"]] == [
        "Kaynak", "Etkinlik", "Tarih/Saat", "Kitle", "Tür", "Rezervasyon durumu", "Takip"]
    assert len(COLUMNS) == 7
    ids = list(tab.tree.get_children())
    assert ids == ["vkv:541", "bk:1", "vkv:9"]
    assert tab.tree.item("vkv:541", "values") == (
        "VKV Gölcük", "Ceviz Adam Müzikali", "28.11.2026 11:00", "Çocuk", "Müzikal",
        "19 Kasım 12:00'da açılacak", FOLLOW_ON)
    assert tab.tree.item("vkv:9", "values")[-1] == FOLLOW_OFF
    assert "off" in tab.tree.item("vkv:9", "tags")      # greyed
    assert row_values(view(followed=False))[-1] == FOLLOW_OFF


def test_tab_toggle_by_button_key_and_cell_click(tab, ui):
    tab.tree.selection_set("vkv:541")
    tab.toggle_btn.invoke()
    ui.pump()
    assert ui.toggles == [("vkv:541", False)]
    assert tab.tree.item("vkv:541", "values")[-1] == FOLLOW_OFF
    assert "off" in tab.tree.item("vkv:541", "tags")
    assert tab.selected_view()["id"] == "vkv:541"       # selection survived the refresh
    tab.toggle_selected()          # what Space / Enter on the list call
    ui.pump()
    assert ui.toggles[-1] == ("vkv:541", True)
    # a click on the Takip cell toggles that row (also a not-followed one)
    tab.toggle("vkv:9")
    ui.pump()
    assert ui.toggles[-1] == ("vkv:9", True)


def test_tab_open_page_and_empty_state(tab, ui):
    tab.tree.selection_set("bk:1")
    tab.open_btn.invoke()
    assert ui.opened == ["https://example.test/bk:1"]
    ui.views.clear()
    ui.refresh_rows()
    assert tab.tree.get_children() == ()
    assert tab.empty.winfo_manager() == "place"
    ui.views.append(view())
    ui.refresh_rows()
    assert tab.empty.winfo_manager() == ""


def test_tab_is_rebuilt_only_when_something_changed(tab, ui):
    ui.refresh_rows()
    before = tab._sig
    first = tab.tree.get_children()
    ui.refresh_rows()
    assert tab._sig is before and tab.tree.get_children() == first


def test_tab_survives_service_errors_and_a_service_without_the_method(tab, ui):
    def boom(now=None):
        raise RuntimeError("x")
    ui.svc.event_views = boom
    tab._sig = None
    ui.refresh_rows()                                  # must not raise
    del ui.svc.__dict__["event_views"]
    tab._sig = None
    ui.refresh_rows()


def test_failed_save_is_reported(tab, ui):
    def broken(eid, on):
        raise OSError("disk")
    ui.svc.set_event_follow = broken
    tab.toggle("vkv:541")
    ui.pump()
    assert ui.box.shown[-1][0] == "error"


def test_click_on_the_follow_cell_toggles_other_cells_do_not(tab, ui, monkeypatch):
    """The window is withdrawn in tests (no geometry), so Tk's hit-testing is stubbed."""
    import types
    hit = {"region": "cell", "column": "#7", "row": "bk:1"}
    monkeypatch.setattr(tab.tree, "identify_region", lambda x, y: hit["region"])
    monkeypatch.setattr(tab.tree, "identify_column", lambda x: hit["column"])
    monkeypatch.setattr(tab.tree, "identify_row", lambda y: hit["row"])
    click = types.SimpleNamespace(x=1, y=1)
    tab._click(click)
    ui.pump()
    assert ui.toggles == [("bk:1", False)] and tab.selected_view()["id"] == "bk:1"
    hit["column"] = "#2"                                   # title cell
    tab._click(click)
    hit.update(column="#7", region="heading")              # header
    tab._click(click)
    hit.update(region="cell", row="")                      # empty area below the rows
    tab._click(click)
    ui.pump()
    assert ui.toggles == [("bk:1", False)]
