"""Wave 22 A: "Yeni gelişmeler" -- the private list of what the generic popup was about."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

tk = pytest.importorskip("tkinter")

from pc import alerts as AL  # noqa: E402
from pc import app_logic as L  # noqa: E402
from pc import ui as appmod  # noqa: E402
from tests.test_pc_app_smoke import Box, FakeService, tk_root, ui  # noqa: E402,F401
from tiyatro.models import ISTANBUL, Event  # noqa: E402

SECRET = "On İkinci Gece"


def _sale(key="k1", slug="p1", **extra):
    return Event("ON_SALE", key, slug, "a", ["y"],
                 {"title": SECRET, "url": "https://secret.example/x",
                  "start": "2026-11-19T20:00:00+03:00", "remaining": 3, **extra})


def _src(kind="SRC_NEW", key="s1", eid="bk:1"):
    return Event(kind, key, "", None, [], {
        "source": "basiskele", "event_id": eid, "title": "Püsküllü Muamma", "kind": "Tiyatro",
        "start": "2026-11-20T20:00:00+03:00", "venue": "Başiskele",
        "opens_at": "2026-11-19T12:00:00+03:00", "url": "https://x.test"})


# ---- logic ------------------------------------------------------------------------------------
def test_line_texts_follow_the_requested_wording():
    assert AL.line_text(AL.describe(_sale())) == \
        "🎟 Bilet açıldı — On İkinci Gece · 19 Kas Per 20:00 · son 3 bilet"
    assert AL.line_text(AL.describe(_src())) == \
        "🆕 Yeni etkinlik — Püsküllü Muamma · Başiskele · 20 Kas Cum 20:00"
    soon = AL.line_text(AL.describe(_src("SRC_SOON")))
    assert soon.startswith("⏰ Rezervasyon açılıyor — Püsküllü Muamma") and "açılış 19 Kas Per 12:00" in soon
    health = AL.describe(Event("PC_INACTIVE", "h1", "", None, [], {}))
    assert AL.line_text(health) == "🖥 PC uygulaması aktif değil" and health["slug"] == ""


def test_row_ids_of_alerts():
    assert AL.describe(_sale())["slug"] == "p1"               # a play: its slug
    assert AL.describe(_src())["slug"] == "bk:1"              # a source event: its id


def test_store_add_dedupes_acks_and_persists(tmp_path):
    clock = [datetime(2026, 11, 18, 10, 0, tzinfo=ISTANBUL)]
    st = AL.AlertStore(clock=lambda: clock[0])
    assert st.add([_sale("k1"), _src()]) == 2
    assert st.add([_sale("k1")]) == 0                          # same event key: once
    assert st.count() == 2 and [i["key"] for i in st.open()] == ["s1", "k1"]   # newest first
    assert st.open_slugs() == {"p1", "bk:1"}
    assert st.ack_slug("p1") == 1 and st.open_slugs() == {"bk:1"}
    path = tmp_path / "ui.json"
    path.write_text(json.dumps({"alerts": st.to_json()}), encoding="utf-8")
    again = AL.AlertStore.load(path, clock=lambda: clock[0])
    assert again.count() == 1 and again.add([_sale("k1")]) == 0   # acknowledged ones are remembered
    clock[0] += timedelta(days=4)
    old = AL.AlertStore(again.to_json(), clock=lambda: clock[0])
    assert [i["key"] for i in old.items] == ["s1"]             # acknowledged > 3 days: dropped
    assert old.ack_all() == 1 and old.count() == 0


def test_store_ignores_garbage_and_caps_size():
    st = AL.AlertStore([None, 1, {"key": ""}, {"key": "a", "kind": "ON_SALE", "ack": 5}, "x"])
    assert [i["key"] for i in st.items] == ["a"] and st.items[0]["ack"] is None
    big = AL.AlertStore()
    for n in range(AL.MAX_ITEMS + 30):
        big.add([Event("ON_SALE", f"k{n}", "p", "a", [], {"title": "T"})])
    assert len(big.items) == AL.MAX_ITEMS and big.count() == AL.MAX_ITEMS
    assert AL.AlertStore.load("does-not-exist.json").count() == 0


def test_row_tags_get_alert_only_for_live_rows():
    class R:
        is_removed = watched = is_new = False
        alert = True
    assert "alert" in L.row_tags(R())
    R.is_removed = True
    assert "alert" not in L.row_tags(R())


# ---- window ------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _mapped(ui):
    ui.root.deiconify()
    ui.root.update()


def _fire(ui, *events):
    for cb in ui.svc.callbacks:
        cb("t", "x", list(events))
    ui.pump()
    ui.root.update()


def _bar_text(ui):
    return [lbl.plain for lbl, _lk in ui._alert_pool[:len(ui._alert_shown)]]


def test_banner_hidden_without_alerts_then_lists_details_while_popup_stays_generic(ui):
    ui.root.update()
    assert not ui.alert_bar.winfo_ismapped()
    _fire(ui, _sale())
    ui.flush_popups()
    ui.root.update()
    assert ui.alert_bar.winfo_ismapped()
    assert ui.alert_title.cget("text") == "Yeni gelişmeler (1)"
    assert _bar_text(ui) == ["🎟 Bilet açıldı — On İkinci Gece · 19 Kas Per 20:00 · son 3 bilet"]
    assert ui.popup.label.cget("text") == "Tiyatro Takip: bilet açıldı"   # generic, unchanged
    assert SECRET not in ui.popup.label.cget("text")
    ui.popup.dismiss()


def test_row_is_marked_until_the_alert_is_read(ui):
    _fire(ui, _sale(slug="p2"))
    assert "alert" in ui.tree.item("p2")["tags"] and "alert" not in ui.tree.item("p1")["tags"]
    ui.alert_ack_btn.invoke()
    ui.root.update()
    assert "alert" not in ui.tree.item("p2")["tags"]
    assert not ui.alert_bar.winfo_ismapped()


def test_clicking_a_line_selects_the_row_opens_detail_and_reads_the_alert(ui):
    _fire(ui, _sale(slug="p2"), _sale("k2", slug="p1"))
    assert ui.alerts.count() == 2
    newest = ui._alert_shown[0]["slug"]
    ui._alert_pool[0][1].invoke()                  # "Göster ›" of the first line
    ui.root.update()
    assert ui.selected == newest and ui.detail_body is not None
    assert ui.alerts.count() == 1 and ui.alert_bar.winfo_ismapped()
    ui._alert_pool[0][1].invoke()
    ui.root.update()
    assert ui.alerts.count() == 0 and not ui.alert_bar.winfo_ismapped()


def test_double_click_on_the_text_does_the_same(ui):
    _fire(ui, _sale(slug="p2"))
    lbl = ui._alert_pool[0][0]
    assert lbl.bind("<Double-Button-1>")          # (Tk cannot synthesise a double click)
    ui._alert_clicked(0)                          # what that binding calls
    ui.root.update()
    assert ui.selected == "p2" and ui.alerts.count() == 0


def test_opening_the_row_from_the_list_reads_its_alerts(ui):
    _fire(ui, _sale(slug="p2"), _sale("k2", slug="p1"))
    ui.select_slug("p2")
    ui.root.update()
    assert ui.alerts.open_slugs() == {"p1"} and "alert" not in ui.tree.item("p2")["tags"]


def test_alert_for_a_row_that_is_gone_says_so_and_is_read(ui):
    _fire(ui, _sale(slug="gone-play"))
    ui._alert_pool[0][1].invoke()
    assert ui.alerts.count() == 0 and "listede yok" in ui.msg_var.get()


def test_persisted_in_ui_json_and_restored_after_restart(ui, tk_root):
    _fire(ui, _sale(slug="p2"), _src())
    saved = json.loads((ui.local_dir / "ui.json").read_text(encoding="utf-8"))
    assert len(saved["alerts"]) == 2 and all(a["ack"] is None for a in saved["alerts"])
    ui.ack_alerts()
    saved = json.loads((ui.local_dir / "ui.json").read_text(encoding="utf-8"))
    assert all(a["ack"] for a in saved["alerts"])
    _fire(ui, _sale("k9", slug="p1"))
    ui.closing = True
    for w in list(tk_root.winfo_children()):
        w.destroy()
    tk_root.configure(menu=tk.Menu(tk_root))
    again = appmod.App(tk_root, FakeService(), ui.local_dir, open_url=lambda u: None,
                       beep=lambda: None, msgbox=Box(), inline=True)
    again.refresh_rows()
    tk_root.update()
    assert again.alerts.count() == 1                       # the unread one survived, the read ones did not
    assert again.alert_bar.winfo_ismapped()
    assert "alert" in again.tree.item("p1")["tags"]
    again.closing = True


def test_popup_open_brings_the_banner_into_view(ui):
    _fire(ui, _sale())
    ui.flush_popups()
    ui.notebook.select(1)                                  # user is on the log tab
    ui.root.update()
    ui.popup.open_main()
    ui.root.update()
    assert ui.notebook.index(ui.notebook.select()) == 0
    assert ui.alert_bar.winfo_ismapped() and ui.alert_bar.winfo_viewable()


def test_banner_text_is_selectable_and_copyable(ui):
    _fire(ui, _sale())
    lbl = ui._alert_pool[0][0]
    lbl.select_all()
    assert lbl.copy_selection()
    assert "On İkinci Gece" in ui.root.clipboard_get()


def test_many_alerts_show_the_newest_few_and_a_count(ui):
    _fire(ui, *[_sale(f"k{n}", slug="p1") for n in range(AL.MAX_LINES + 3)])
    assert len(ui._alert_shown) == AL.MAX_LINES
    assert ui.alert_more.winfo_ismapped() and "3 gelişme daha" in ui.alert_more.plain


def test_banner_geometry_fits_and_does_not_jitter(ui):
    ui.root.geometry("1100x760")
    ui.root.update()
    _fire(ui, _sale(slug="p2"), _src(), _sale("k3", slug="p1", title="Çok Uzun " * 20))
    for _ in range(3):
        ui.root.update()
    bar, tab = ui.alert_bar, ui.games_tab
    assert bar.winfo_ismapped() and bar.winfo_width() <= tab.winfo_width()
    for lbl, link in ui._alert_pool[:3]:
        assert lbl.winfo_x() + lbl.winfo_width() <= bar.winfo_width()          # nothing clipped
        assert link.winfo_x() + link.winfo_width() <= bar.winfo_width()
        assert lbl.winfo_height() >= lbl.winfo_reqheight() - 1
    before = (bar.winfo_width(), bar.winfo_height(), ui.pane.winfo_height(),
              [(l.winfo_width(), l.winfo_height()) for l, _k in ui._alert_pool[:3]])
    for _ in range(5):
        ui._render_alerts()
        ui._rewrap_alerts()
        ui.root.update()
    after = (bar.winfo_width(), bar.winfo_height(), ui.pane.winfo_height(),
             [(l.winfo_width(), l.winfo_height()) for l, _k in ui._alert_pool[:3]])
    assert before == after
    # the list keeps a usable height next to the banner
    assert ui.tree.winfo_height() >= ui.tree.head_px + ui.tree.row_px


def test_banner_cuts_a_long_line_in_a_narrow_window_without_widening_it(ui):
    ui.root.geometry(f"{L.MIN_WINDOW[0]}x760")
    ui.root.update()
    _fire(ui, _sale(slug="p2", title="Çok Uzun Bir Oyun Adı " * 6))
    for _ in range(3):
        ui.root.update()
    lbl = ui._alert_pool[0][0]
    assert lbl.winfo_x() + lbl.winfo_width() <= ui.alert_bar.winfo_width()
    assert int(lbl.cget("height")) == 1                      # wave 23: one line, cut with an ellipsis
    assert lbl.get_text().endswith("…") if hasattr(lbl, "get_text") else True


def test_unchanged_alerts_do_not_rebuild(ui):
    _fire(ui, _sale())
    key = ui._alert_key
    n = ui.rows_rebuilt
    ui._render_alerts()
    ui.refresh_rows()
    assert ui._alert_key == key and ui.rows_rebuilt == n


@pytest.mark.parametrize("count", [1, 3, 12])
def test_banner_height_is_capped_whatever_the_number_of_alerts(ui, count):
    """Wave 23: at most 3 one-line entries, the rest behind "… ve N gelişme daha"; the list
    never loses more than ~110 px and 12 alerts take no more room than 3."""
    ui.root.update()
    base = ui.tree.winfo_height()
    _fire(ui, *[_sale(key=f"k{i}", slug=f"p{i}", title=f"Oyun {i}") for i in range(count)])
    for _ in range(3):
        ui.root.update()
    assert len(ui._alert_shown) == min(count, AL.MAX_LINES) == min(count, 3)
    lost = base - ui.tree.winfo_height()
    bar = ui.alert_bar.winfo_height() + 6                       # strip + its padding
    assert 0 < ui.alert_bar.winfo_height() <= 110 and 0 <= lost <= 110, (bar, lost)
    if count > 3:
        assert ui.alert_more.winfo_ismapped()
        assert f"… ve {count - 3} gelişme daha" in ui.alert_more.plain
    again = ui.tree.winfo_height()
    ui.root.update()
    assert ui.tree.winfo_height() == again                      # no jitter
    _CAPS[count] = ui.alert_bar.winfo_height()
    if len(_CAPS) == 3:
        assert abs(_CAPS[12] - _CAPS[3]) <= 2


_CAPS: dict = {}


def test_legacy_portal_changed_alerts_are_dropped_on_load():
    from pc.alerts import AlertStore
    saved = [{"key": "PORTAL_CHANGED:basiskele:abc", "kind": "PORTAL_CHANGED",
              "label": "Portal güncellendi", "detail": "5 etkinlik",
              "ts": "2026-11-18T15:03:52+03:00", "ack": None}]
    assert AlertStore(saved).count() == 0
