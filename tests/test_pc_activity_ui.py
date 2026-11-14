"""Headless Tk smoke tests of the "Etkinlik günlüğü" tab (fake service, tmp dirs only)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

tk = pytest.importorskip("tkinter")

from pc import activity as A  # noqa: E402
from pc import ui as appmod  # noqa: E402
from tests.test_pc_app_smoke import Box, FakeService, tk_root  # noqa: E402,F401
from tiyatro.models import ISTANBUL  # noqa: E402

T0 = datetime(2026, 11, 14, 14, 35, 10, tzinfo=ISTANBUL)


class Clock:
    def __init__(self):
        self.t = datetime.now(ISTANBUL).replace(microsecond=0)

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


def build(tk_root, tmp_path, ui_json=None):
    root = tk_root
    for w in root.winfo_children():
        w.destroy()
    root.configure(menu=tk.Menu(root))
    if ui_json is not None:
        (tmp_path / "ui.json").write_text(ui_json, encoding="utf-8")
    box, revealed = Box(), []
    app = appmod.App(root, FakeService(), tmp_path, open_url=lambda u: None,
                     beep=lambda: None, msgbox=box, inline=True, reveal=revealed.append)
    app.activity._clock = Clock()
    app.box, app.revealed = box, revealed
    return app


@pytest.fixture
def ui(tmp_path, tk_root):
    app = build(tk_root, tmp_path)
    app.refresh_rows()
    try:
        yield app
    finally:
        app.closing = True
        for w in list(tk_root.winfo_children()):
            w.destroy()


def seed(app):
    a = app.activity
    a.add("Liste kontrolü", "6 oyun, 17 seans (3 satışta, 1 yakında, 13 tükendi)",
          "değişiklik yok\nPC (otomatik) · Normal mod")
    a.add("Bilet açılışı", "Bilet alınabilir! — Hamlet", "Seans: 21 Kas Cmt 20:00")
    a.add("Hata/Uyarı", "Biletinial okunamadı", "", "warn")
    a.add("Hata/Uyarı", "Kayıt yazılamadı", "disk dolu", "error")
    a.add("Takip", "PC'den: Hamlet takibi açıldı")
    app.activity_tab.refresh()


def shown(app):
    tab = app.activity_tab
    return [tab.tree.item(i, "values") for i in tab.tree.get_children()]


def test_window_has_two_tabs_and_the_games_tab_still_works(ui):
    nb = ui.notebook
    assert [nb.tab(t, "text") for t in nb.tabs()] == ["Oyunlar", "Etkinlik günlüğü"]
    assert ui.tree.get_children()[0] == "p1"  # list unchanged
    ui.select_slug("p1")
    assert len(ui.session_tree.get_children()) == 3
    ui.sort_by("title")
    assert ui.sort == ("title", "asc")


def test_log_columns_newest_first_with_plain_text(ui):
    seed(ui)
    tab = ui.activity_tab
    assert [tab.tree.heading(c)["text"] for c in tab.tree["columns"]] == [
        "Zaman", "Kategori", "Özet", "Sonuç"]
    rows = shown(ui)
    assert [r[2] for r in rows][:2] == ["PC'den: Hamlet takibi açıldı", "Kayıt yazılamadı"]
    assert len(rows) == 5
    last = rows[-1]
    assert last[1] == "Liste kontrolü" and last[3] == "değişiklik yok"
    assert ":" in last[0]  # a time of day


def test_rows_are_colour_coded_by_level(ui):
    seed(ui)
    tab = ui.activity_tab
    tags = {tab.tree.item(i, "values")[2]: tab.tree.item(i, "tags") for i in
            tab.tree.get_children()}
    assert "error" in tags["Kayıt yazılamadı"] and "warn" in tags["Biletinial okunamadı"]
    assert "error" not in tags["PC'den: Hamlet takibi açıldı"]


def test_category_checkboxes_filter_and_persist(ui, tmp_path):
    seed(ui)
    tab = ui.activity_tab
    assert all(v.get() for v in tab.cat_vars.values())  # all on by default
    assert set(tab.cat_vars) == set(A.CATEGORIES)
    tab.cat_vars["Hata/Uyarı"].set(False)
    tab.filters_changed()
    assert len(shown(ui)) == 3
    saved = json.loads((tmp_path / "ui.json").read_text(encoding="utf-8"))["activity"]
    assert "Hata/Uyarı" not in saved["cats"] and "Takip" in saved["cats"]
    for var in tab.cat_vars.values():
        var.set(False)
    tab.filters_changed()
    assert shown(ui) == []
    assert "kayıt" in tab.count_var.get()


def test_level_selector(ui):
    seed(ui)
    tab = ui.activity_tab
    assert tab.level_var.get() == "Hepsi"
    assert list(tab.level_box["values"]) == ["Hepsi", "Uyarı ve hata"]
    tab.level_var.set("Uyarı ve hata")
    tab.filters_changed()
    assert [r[2] for r in shown(ui)] == ["Kayıt yazılamadı", "Biletinial okunamadı"]


def test_search_filters_live_and_ignores_turkish_letters(ui):
    seed(ui)
    tab = ui.activity_tab
    tab.search_var.set("HAMLET")  # the trace refreshes without any button
    assert [r[2] for r in shown(ui)] == ["PC'den: Hamlet takibi açıldı",
                                        "Bilet alınabilir! — Hamlet"]
    tab.search_var.set("disk dolu")
    assert [r[2] for r in shown(ui)] == ["Kayıt yazılamadı"]
    tab.search_var.set("kontrolu")
    assert len(shown(ui)) == 1
    tab.search_var.set("")
    assert len(shown(ui)) == 5


def test_clear_asks_first(ui):
    seed(ui)
    ui.box.answer = False
    ui.activity_tab.clear_btn.invoke()
    assert len(shown(ui)) == 5 and ui.box.shown[-1][0] == "ask"
    ui.box.answer = True
    ui.activity_tab.clear_btn.invoke()
    assert shown(ui) == [] and ui.activity.load() == []


def test_show_file_button_reveals_the_log_file(ui, tmp_path):
    ui.activity_tab.reveal_btn.invoke()
    assert ui.revealed == [tmp_path / "activity.jsonl"]


def test_detail_dialog_shows_the_full_text(ui):
    seed(ui)
    tab = ui.activity_tab
    iid = next(i for i in tab.tree.get_children()
               if tab.tree.item(i, "values")[1] == "Liste kontrolü")
    tab.tree.selection_set(iid)
    dlg = tab.open_detail()
    assert dlg is not None
    text = dlg.body.get("1.0", "end")
    assert "6 oyun, 17 seans" in text and "PC (otomatik) · Normal mod" in text
    assert "Liste kontrolü" in text and "Bilgi" in text
    assert str(dlg.body.cget("state")) == "disabled"
    dlg.destroy()
    tab.tree.selection_remove(iid)
    assert tab.open_detail() is None  # nothing selected


def test_new_entries_appear_on_poll_without_losing_selection(ui):
    seed(ui)
    tab = ui.activity_tab
    old = next(i for i in tab.tree.get_children()
               if tab.tree.item(i, "values")[2] == "Biletinial okunamadı")
    tab.tree.selection_set(old)
    ui.activity.add("Takip", "PC'den: yeni satır")
    assert len(shown(ui)) == 5  # not shown until the poll
    tab.poll()
    assert shown(ui)[0][2] == "PC'den: yeni satır" and len(shown(ui)) == 6
    assert tab.tree.selection() == (old,)
    n = tab.refresh_count
    tab.poll()  # nothing new: the tree is not rebuilt
    assert tab.refresh_count == n


def test_a_tick_refreshes_the_log(ui):
    seed(ui)
    ui.activity.add("Takip", "PC'den: tick sonrası")
    res = type("R", (), {"errors": []})()
    ui._on_tick(res)
    assert shown(ui)[0][2] == "PC'den: tick sonrası"


def test_filters_restored_from_ui_json(tk_root, tmp_path):
    app = build(tk_root, tmp_path, json.dumps({"activity": {
        "cats": ["Takip"], "level": "warn", "text": "ışık"}}))
    try:
        tab = app.activity_tab
        assert [c for c, v in tab.cat_vars.items() if v.get()] == ["Takip"]
        assert tab.level_var.get() == "Uyarı ve hata" and tab.search_var.get() == "ışık"
    finally:
        app.closing = True


def test_corrupt_ui_json_gives_default_filters(tk_root, tmp_path):
    app = build(tk_root, tmp_path, "{broken")
    try:
        tab = app.activity_tab
        assert all(v.get() for v in tab.cat_vars.values())
        assert tab.level_var.get() == "Hepsi" and tab.search_var.get() == ""
    finally:
        app.closing = True


def test_save_ui_keeps_sort_beep_and_adds_filters(ui, tmp_path):
    ui.sort_by("title")
    ui.activity_tab.search_var.set("x")
    ui.save_ui()
    data = json.loads((tmp_path / "ui.json").read_text(encoding="utf-8"))
    assert data["sort"] == ["title", "asc"] and data["beep"] is True
    assert data["activity"]["text"] == "x"


def test_service_log_is_shared_when_the_service_has_one(tk_root, tmp_path):
    svc = FakeService()
    svc.activity = A.ActivityLog(tmp_path / "shared.jsonl")
    root = tk_root
    for w in root.winfo_children():
        w.destroy()
    root.configure(menu=tk.Menu(root))
    app = appmod.App(root, svc, tmp_path, msgbox=Box(), inline=True, reveal=lambda p: None)
    try:
        assert app.activity is svc.activity
    finally:
        app.closing = True


def test_close_asks_the_service_to_log_shutdown(tk_root, tmp_path):
    svc = FakeService()
    calls = []
    svc.log_shutdown = lambda: calls.append("stop")
    root = tk_root
    for w in root.winfo_children():
        w.destroy()
    root.configure(menu=tk.Menu(root))
    app = appmod.App(root, svc, tmp_path, msgbox=Box(), inline=True, reveal=lambda p: None)
    app.close(destroy=False)
    assert calls == ["stop"]
