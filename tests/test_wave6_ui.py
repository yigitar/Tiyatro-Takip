"""Wave 6 window tests (headless Tk): the GridTable list, Takip checkbox cells, link buttons,
per-session buttons, source "Kaynağı aç" buttons, provenance styling, new-item highlighting."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

tk = pytest.importorskip("tkinter")

from pc import app_logic as L  # noqa: E402
from pc.table import DERIVED_COLOR, GridTable  # noqa: E402
from pc.viewmodel import Row, with_events  # noqa: E402
from tests.test_pc_app_smoke import Box, FakeService, iso, tk_root, ui  # noqa: E402,F401
from tests.test_wave6 import _view  # noqa: E402
from tiyatro.models import ISTANBUL  # noqa: E402


# ---- GridTable ---------------------------------------------------------------------------------

def make_table(tk_root, **kw):
    frame = tk.Frame(tk_root)
    cols = (("a", "A", 80), ("b", "B", 120), ("c", "C", 60))
    table = GridTable(frame, cols, stretch=("b",), **kw)
    table.pack(fill="both", expand=True)
    frame.pack(fill="both", expand=True)
    return frame, table


def canvas_texts(table):
    body = table.body
    return [(body.itemcget(i, "text"), str(body.itemcget(i, "font")), body.itemcget(i, "fill"))
            for i in body.find_all() if body.type(i) == "text"]


def test_table_treeview_like_api_and_selection_event(tk_root):
    frame, t = make_table(tk_root)
    seen = []
    t.bind("<<TreeviewSelect>>", lambda e: seen.append(t.selection()))
    t.insert("", "end", iid="r1", cells=[L.Cell("x"), L.Cell("y", True), L.Cell("z")], tags=("w",))
    t.insert("", "end", iid="r2", values=("p", "q", "r"))
    assert t["columns"] == ("a", "b", "c") and t.get_children() == ("r1", "r2")
    assert t.item("r1", "values") == ("x", "y", "z") and t.item("r1", "tags") == ("w",)
    t.selection_set("r2")
    tk_root.update()
    assert t.selection() == ("r2",) and seen == [("r2",)]
    t.set_tags("r1", ("a", "b"))
    assert t.item("r1", "tags") == ("a", "b")
    t.heading("a", text="A ▲")
    assert t.heading("a")["text"] == "A ▲" and t.heading("b")["command"]
    t.delete(*t.get_children())
    assert t.get_children() == () and t.selection() == ()
    frame.destroy()


def test_table_draws_derived_cells_italic_grey_blue_and_the_rest_normal(tk_root):
    frame, t = make_table(tk_root)
    t.tag_configure("watched", bold=True)
    t.insert("", "end", iid="r1", cells=[L.Cell("normal"), L.Cell("derived", True),
                                         L.Cell("both", True)], tags=("watched",))
    t.insert("", "end", iid="r2", cells=[L.Cell("plain"), L.Cell("x"), L.Cell("y")])
    t.draw()
    seen = {text: (font, fill) for text, font, fill in canvas_texts(t)}
    assert seen["derived"][1] == DERIVED_COLOR and seen["plain"][1] != DERIVED_COLOR
    import tkinter.font as tkfont
    assert tkfont.Font(font=seen["derived"][0]).actual("slant") == "italic"
    assert tkfont.Font(font=seen["plain"][0]).actual("slant") == "roman"
    assert tkfont.Font(font=seen["normal"][0]).actual("weight") == "bold"   # "watched" row
    assert tkfont.Font(font=seen["both"][0]).actual("weight") == "bold"
    assert tkfont.Font(font=seen["both"][0]).actual("slant") == "italic"
    frame.destroy()


def test_table_click_on_check_and_link_cells_does_not_select(tk_root):
    clicks = []
    frame, t = make_table(tk_root, on_cell=lambda iid, key: clicks.append((iid, key)))
    t.insert("", "end", iid="r1", cells=[L.Cell("☐", False, "check"), L.Cell("t"),
                                         L.Cell("Oyun ↗", False, "link")])
    t.draw()
    tk_root.update()
    widths, xs = t._layout()

    class E:  # a body click at the given x, in the first row
        def __init__(self, x):
            self.x, self.y = x, 5
    assert t._hit(E(5)) == ("r1", "a") and t._hit(E(xs[2] + 5)) == ("r1", "c")
    t._click(E(5))
    t._click(E(xs[2] + 5))
    assert clicks == [("r1", "a"), ("r1", "c")] and t.selection() == ()
    t._click(E(xs[1] + 5))                           # a plain cell selects
    assert t.selection() == ("r1",) and len(clicks) == 2
    assert t._hit(E(-1000 + 1000)) == ("r1", "a")
    frame.destroy()


def test_table_column_widths_stretch_and_horizontal_scrollbar_only_when_needed(tk_root):
    frame, t = make_table(tk_root)
    assert t.column_widths(500) == [80, 360, 60]        # spare room goes to the stretch column
    assert sum(t.column_widths(100)) >= 100 and all(w >= 44 for w in t.column_widths(100))
    t.insert("", "end", iid="r1", values=("a", "b", "c"))
    frame.configure(width=600, height=200)
    frame.pack_propagate(False)
    tk_root.update()
    t.hbar.set(0.0, 1.0)
    assert t.hbar.winfo_manager() == ""                 # everything fits: no scrollbar
    t.hbar.set(0.0, 0.5)
    assert t.hbar.winfo_manager() == "pack"
    frame.destroy()


def test_table_keyboard_navigation_and_space(tk_root):
    clicks = []
    frame, t = make_table(tk_root, on_cell=lambda iid, key: clicks.append((iid, key)))
    for i in range(3):
        t.insert("", "end", iid=f"r{i}", values=(str(i), "b", "c"))
    t._step(1)
    assert t.selection() == ("r0",)
    t._step(1)
    t._step(10)
    assert t.selection() == ("r2",)
    t._step(-1)
    assert t.selection() == ("r1",)
    frame.destroy()


# ---- the window -------------------------------------------------------------------------------

def cells(ui, slug):
    return dict(zip(ui.tree.keys, [ui.tree.cell(slug, k) for k in ui.tree.keys]))


def test_takip_cell_toggles_a_play_and_flips_at_once(ui):
    assert cells(ui, "p2")["follow"].text == L.UNCHECKED
    ui._cell_click("p2", "follow")
    assert cells(ui, "p2")["follow"].text == L.CHECKED       # shown before the save is done
    ui.pump()
    assert ("set_active", ("p2", True)) in ui.svc.calls
    ui._cell_click("p1", "follow")
    ui.pump()
    assert ("set_active", ("p1", False)) in ui.svc.calls
    assert cells(ui, "p1")["follow"].text == L.UNCHECKED


def test_takip_cell_ignores_a_second_click_while_the_save_is_pending(ui):
    ui._pending.add(("active", "p2"))
    ui._cell_click("p2", "follow")
    assert cells(ui, "p2")["follow"].text == L.UNCHECKED and ui.svc.calls == []


def test_takip_cell_of_a_source_event_uses_event_follow(ui):
    ui.views = [_view(followed=False)]
    ui.follow_calls = []
    base = ui.svc.rows
    ui.svc.rows = lambda now=None: with_events(base(), ui.views, datetime.now(ISTANBUL))

    def set_follow(eid, on):
        ui.follow_calls.append((eid, on))
        ui.views[0] = dict(ui.views[0], followed=on)
    ui.svc.set_event_follow = set_follow
    ui.refresh_rows()
    assert cells(ui, "vkv:541")["follow"].text == L.UNCHECKED
    ui._cell_click("vkv:541", "follow")
    ui.pump()
    assert ui.follow_calls == [("vkv:541", True)]
    assert cells(ui, "vkv:541")["follow"].text == L.CHECKED


def test_link_cell_opens_the_page_and_other_cells_do_nothing(ui):
    ui._cell_click("p1", "link")
    assert ui.opened == ["https://example.test/p1"]
    ui._cell_click("p1", "title")
    assert len(ui.opened) == 1


def test_detail_session_table_has_a_button_per_session(ui):
    ui.select_slug("p1")
    kids = ui.session_tree.get_children()
    assert len(kids) == 3
    # the fake sessions carry no page of their own: the button opens the play page
    assert ui.session_tree.cell(kids[0], "link").text == "Oyun ↗"
    assert "Seans bağlantısı olmayan" in ui.session_note.cget("text")
    ui._session_click(kids[0], "link")
    assert ui.opened == ["https://example.test/p1"]
    ui.svc._state["plays"]["p1"]["sessions"]["a"]["url"] = "https://example.test/p1/seance-a"
    ui.show_detail(force=True)
    kids = ui.session_tree.get_children()
    first = ui.session_tree.cell(kids[0], "link")
    assert first.text == "Seans ↗" and first.kind == "link"
    ui._session_click(kids[0], "link")
    assert ui.opened[-1] == "https://example.test/p1/seance-a"
    ui._session_click(kids[1], "link")                  # one without a page: the play page
    assert ui.opened[-1] == "https://example.test/p1"


def test_event_detail_has_page_button_and_says_there_are_no_session_pages(ui):
    ui.views = [_view(opens=datetime.now(ISTANBUL) + timedelta(days=3))]
    base = ui.svc.rows
    ui.svc.rows = lambda now=None: with_events(base(), ui.views, datetime.now(ISTANBUL))
    ui.refresh_rows()
    ui.select_slug("vkv:541")
    assert ui.event_page_btn.cget("text").startswith("Etkinlik sayfası")
    ui.event_page_btn.invoke()
    assert ui.opened == ["https://example.test/vkv:541"]
    assert "seans başına ayrı sayfa yok" in ui.event_no_page_label.cget("text")
    assert ui.follow_help.cget("text").startswith("Takip açıkken")
    ui.active_chk.invoke()
    ui.pump()


def test_source_strip_has_an_open_button_for_every_source(ui):
    ui.svc.sources = lambda: [
        {"id": i, "name": n, "status": "ok", "icon": "✅", "last_check": "20:30",
         "last_change": "18:48", "items_count": 3, "error": None}
        for i, n in (("biletinial", "Biletinial"), ("basiskele", "Başiskele Belediyesi"),
                     ("vkv", "VKV Gölcük"))]
    ui.svc.portals = lambda: []
    ui.svc.config.sources = ["https://biletinial.test/mekan/x"]
    ui._source_sig = None
    ui._render_sources()
    assert set(ui.source_open_buttons) == {"biletinial", "basiskele", "vkv"}
    ui.source_open_buttons["vkv"].invoke()
    ui.source_open_buttons["biletinial"].invoke()
    assert ui.opened[0].endswith("/Etkinlikler") and ui.opened[1] == "https://biletinial.test/mekan/x"
    ui.source_open_buttons["basiskele"].invoke()
    assert ui.opened[2].startswith("https://www.basiskele.bel.tr")


def test_legend_and_derived_styling_in_the_detail(ui):
    assert ui.legend_texts[:3] == ["Normal yazı = kaynaktan alınan",
                                   "İtalik = türetilmiş / varsayım",
                                   "Mor kalın = yeni, henüz açılmadı"]
    ui.select_slug("p1")                      # age 7 -> Kitle by the age rule = derived
    tags = ui.meta_label.tag_ranges("derived")
    assert tags, "Kitle is derived (age rule) and must be italic"
    # wave 17: one meta line for plays and events, Kaynak first
    assert ui.meta_label.cget("text").startswith("Kaynak: Biletinial  ·  Kitle: Çocuk  ·  Tür: Komedi")
    plain = ui.meta_label.get("1.0", "end-1c")
    start, end = str(tags[0]), str(tags[1])
    assert plain[int(start.split(".")[1]):int(end.split(".")[1])] == "Kitle: Çocuk"


# ---- new items ---------------------------------------------------------------------------------

def add_play(ui, slug="p3", title="Üçüncü Oyun"):
    ui.svc._state["plays"][slug] = {
        "title": title, "url": f"https://example.test/{slug}", "tags": [], "genre": "Dram",
        "sessions": {"z": {"start": iso(8), "status": "on_sale"}}}
    ui.svc.new_slugs = set()
    ui.refresh_rows()


def test_new_item_after_the_baseline_is_highlighted_until_it_is_opened(ui, tmp_path):
    assert ui.seen.initialised and not any(r.is_new for r in ui.rows)   # upgrade: no flood
    saved = json.loads((tmp_path / "ui.json").read_text(encoding="utf-8"))
    assert set(saved["seen"]) == {"p1", "p2"}
    add_play(ui)
    assert "new" in ui.tree.item("p3", "tags") and "new" not in ui.tree.item("p1", "tags")
    ui.tree.draw()
    bold_purple = [t for t in canvas_texts(ui.tree) if t[0] == "Üçüncü Oyun"]
    assert bold_purple and bold_purple[0][2] != "#111827"          # its own colour
    import tkinter.font as tkfont
    assert tkfont.Font(font=bold_purple[0][1]).actual("weight") == "bold"
    other = [t for t in canvas_texts(ui.tree) if t[0] == "İkinci Oyun"][0]
    assert tkfont.Font(font=other[1]).actual("weight") == "normal"
    ui.refresh_rows()                                              # a tick does not clear it
    assert "new" in ui.tree.item("p3", "tags")
    ui.select_slug("p3")                                           # opened once
    assert "new" not in ui.tree.item("p3", "tags")
    saved = json.loads((tmp_path / "ui.json").read_text(encoding="utf-8"))
    assert "p3" in saved["seen"]
    ui.refresh_rows()
    assert "new" not in ui.tree.item("p3", "tags")


def test_seen_ids_are_per_machine_and_survive_a_restart(ui, tmp_path, tk_root):
    add_play(ui)
    ui.close(destroy=False)
    from pc import ui as appmod
    svc = ui.svc
    app2 = appmod.App(tk_root, svc, tmp_path, open_url=lambda u: None, inline=True)
    app2.refresh_rows()
    assert "new" in app2.tree.item("p3", "tags")        # still unopened after a restart
    assert "new" not in app2.tree.item("p1", "tags")
    app2.closing = True


def test_first_run_with_an_empty_list_does_not_take_the_baseline_too_early(tmp_path, tk_root):
    from pc import ui as appmod

    class Empty(FakeService):
        def __init__(self):
            super().__init__()
            self.loaded = False

        @property
        def state(self):
            return self._state if self.loaded else None

        def rows(self, now=None):
            return super().rows(now) if self.loaded else []

    for w in tk_root.winfo_children():
        w.destroy()
    svc = Empty()
    app = appmod.App(tk_root, svc, tmp_path, open_url=lambda u: None, inline=True)
    app.refresh_rows()
    assert app.seen.initialised is False and not (tmp_path / "ui.json").exists() \
        or "seen" not in json.loads((tmp_path / "ui.json").read_text(encoding="utf-8"))
    svc.loaded = True
    app.refresh_rows()
    assert app.seen.initialised and not any(r.is_new for r in app.rows)
    app.closing = True


def test_row_type_still_has_the_fields_the_table_needs():
    r = Row("s", "T")
    assert r.derived == frozenset() and r.zaman == "" and r.rez_key is None
