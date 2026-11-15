"""Wave 8: single instance (named mutex), autostart (HKCU Run), keep awake, DPI awareness,
status strip on top, flat list styling (no bold for followed rows, zebra, drawn checkbox,
link cells), "Açıklama" legend and the source cards."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from tkinter import font as tkfont

from pc import app_logic as L
from pc import winsetup as W
from tiyatro import sources as S
from tiyatro.models import ISTANBUL

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=ISTANBUL)
windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows only")


# ---- single instance ---------------------------------------------------------------------------

def _name():
    return rf"Local\TiyatroTakipTest-{uuid.uuid4().hex}"


@windows_only


def test_second_instance_is_refused_with_a_named_mutex_and_allowed_after_release():
    name = _name()
    a, b = L.SingleInstance(name), L.SingleInstance(name)
    assert a.acquire() and a.acquire()           # idempotent for the holder
    assert b.acquire() is False
    a.release()
    assert b.acquire() is True
    b.release()


@windows_only


def test_mutex_is_released_by_the_os_when_the_holder_dies_no_stale_lock(tmp_path):
    name = _name()
    code = ("import sys,time;sys.path.insert(0,sys.argv[2]);from pc import app_logic as L;"
            "g=L.SingleInstance(sys.argv[1]);assert g.acquire();print('held',flush=True);"
            "time.sleep(60)")
    child = subprocess.Popen([sys.executable, "-c", code, name, str(REPO)],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        mine = L.SingleInstance(name)
        assert mine.acquire() is False           # a real other process holds it
        child.kill()                             # crash: no release, no cleanup
        child.wait(timeout=10)
        import time
        for _ in range(40):                      # the kernel closes the dead process' handles
            if mine.acquire():
                break
            time.sleep(0.05)
        assert mine.held
        mine.release()
    finally:
        child.kill()
        child.stdout.close()


def test_real_mutex_name_is_per_session_and_stable():
    assert L.MUTEX_NAME == r"Local\TiyatroTakipPC"


class FakeUser32:
    def __init__(self, found=True, iconic=False):
        self.found, self.iconic, self.calls = found, iconic, []

    def FindWindowW(self, cls, title):
        self.calls.append(("find", cls, title))
        return 77 if self.found else 0

    def IsIconic(self, h):
        return self.iconic

    def ShowWindow(self, h, cmd):
        self.calls.append(("show", h, cmd))

    def SetForegroundWindow(self, h):
        self.calls.append(("fg", h))


def test_existing_window_is_restored_and_brought_to_front():
    api = FakeUser32(iconic=True)
    assert W.focus_existing_window("Tiyatro Takip", api) is True
    assert ("find", "TkTopLevel", "Tiyatro Takip") in api.calls
    assert ("show", 77, 9) in api.calls and ("fg", 77) in api.calls   # SW_RESTORE
    api = FakeUser32(iconic=False)
    assert W.focus_existing_window("Tiyatro Takip", api) is True
    assert ("show", 77, 5) in api.calls


def test_no_existing_window_found_reports_false():
    assert W.focus_existing_window("Tiyatro Takip", FakeUser32(found=False)) is False


def test_main_refuses_to_start_and_focuses_the_running_window(monkeypatch):
    from pc import ui
    calls = []

    class Held:
        def acquire(self):
            return False
    monkeypatch.setattr(ui.L, "SingleInstance", Held)
    monkeypatch.setattr(ui.winsetup, "enable_dpi_awareness", lambda: calls.append("dpi"))
    monkeypatch.setattr(ui.winsetup, "focus_existing_window",
                        lambda title: calls.append(("focus", title)) or True)
    monkeypatch.setattr(ui.winsetup, "info_box", lambda *a: calls.append("box"))
    monkeypatch.setattr(ui.tk, "Tk", lambda: pytest.fail("a second window must not be built"))
    assert ui.main() == 0
    assert calls == ["dpi", ("focus", "Tiyatro Takip")]


def test_main_shows_a_message_when_the_other_window_cannot_be_found(monkeypatch):
    from pc import ui
    calls = []

    class Held:
        def acquire(self):
            return False
    monkeypatch.setattr(ui.L, "SingleInstance", Held)
    monkeypatch.setattr(ui.winsetup, "enable_dpi_awareness", lambda: "")
    monkeypatch.setattr(ui.winsetup, "focus_existing_window", lambda title: False)
    monkeypatch.setattr(ui.winsetup, "info_box", lambda *a: calls.append(a))
    assert ui.main() == 0
    assert calls and "zaten çalışıyor" in calls[0][1]


# ---- autostart (HKCU Run) ----------------------------------------------------------------------

class FakeReg:
    """Just enough of winreg, one key."""
    HKEY_CURRENT_USER, KEY_READ, KEY_SET_VALUE, REG_SZ = "HKCU", 1, 2, 1

    def __init__(self):
        self.values, self.writes = {}, []

    class _H:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(self, root, key, res, access):
        assert root == "HKCU"
        return self._H()

    CreateKeyEx = OpenKey

    def QueryValueEx(self, handle, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, handle, name, res, kind, value):
        self.values[name] = value
        self.writes.append(("set", name, value))

    def DeleteValue(self, handle, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]
        self.writes.append(("del", name))


def test_autostart_command_is_windowless_pythonw_in_the_project_dir():
    py = REPO / ".venv" / "Scripts" / "pythonw.exe"
    cmd = W.autostart_command(REPO, py)
    assert cmd.startswith(f'"{py}" -c "') and "pythonw.exe" in cmd
    assert "python.exe" not in cmd.replace("pythonw.exe", "")
    assert f"os.chdir(r'{REPO}')" in cmd and "run_module('pc.app'" in cmd
    assert '"' not in cmd[cmd.index("-c ") + 4:-1]      # one well-formed quoted argument


def test_autostart_refuses_a_path_that_would_break_the_command(tmp_path):
    bad = tmp_path / "it's"
    with pytest.raises(OSError):
        W.autostart_command(bad, tmp_path / "pythonw.exe")


def test_autostart_toggle_writes_then_removes_the_value_and_reflects_the_registry():
    reg = FakeReg()
    assert W.autostart_enabled(reg) is False
    assert W.set_autostart(True, reg=reg, python=REPO / "pythonw.exe") is True
    assert W.autostart_enabled(reg) is True
    assert reg.values[W.RUN_VALUE].startswith('"') and "pc.app" in reg.values[W.RUN_VALUE]
    assert W.set_autostart(False, reg=reg) is False
    assert reg.values == {} and W.autostart_enabled(reg) is False
    W.set_autostart(False, reg=reg)                      # already absent: no error


@windows_only


def test_autostart_against_the_real_registry_on_a_throwaway_key():
    key, name = rf"Software\TiyatroTakipTest{uuid.uuid4().hex}", "Probe"
    import winreg
    try:
        assert W.autostart_enabled(key=key, name=name) is False
        assert W.set_autostart(True, key=key, name=name, python=REPO / "pythonw.exe") is True
        assert "pc.app" in W.autostart_value(key=key, name=name)
        assert W.set_autostart(False, key=key, name=name) is False
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass


def test_the_real_run_key_is_the_current_user_one():
    assert W.RUN_KEY == r"Software\Microsoft\Windows\CurrentVersion\Run"


# ---- keep awake / DPI ----------------------------------------------------------------------------

class FakeKernel:
    def __init__(self, result=1):
        self.result, self.flags = result, []

    def SetThreadExecutionState(self, flags):
        self.flags.append(flags)
        return self.result


def test_keep_awake_sets_continuous_system_required_and_resets():
    k = FakeKernel()
    assert W.set_keep_awake(True, k) is True
    assert W.set_keep_awake(False, k) is True
    assert k.flags == [0x80000001, 0x80000000]           # no ES_DISPLAY_REQUIRED
    assert W.set_keep_awake(True, FakeKernel(0)) is False


class FakeDpi:
    class _Shcore:
        result = 0

        def SetProcessDpiAwareness(self, v):
            assert v == 1
            return self.result

    class _User32:
        called = False

        def SetProcessDPIAware(self):
            self.called = True

    def __init__(self, shcore_result=0):
        self.shcore, self.user32 = self._Shcore(), self._User32()
        self.shcore.result = shcore_result


def test_dpi_awareness_prefers_shcore_then_falls_back():
    assert W.enable_dpi_awareness(FakeDpi(0)) == "shcore"
    api = FakeDpi(-2147024891)                           # E_ACCESSDENIED / already set
    assert W.enable_dpi_awareness(api) == "user32" and api.user32.called


# ---- window: Settings, strip, styling, legend, cards -----------------------------------------------

tk = pytest.importorskip("tkinter")
from tests.test_pc_app_smoke import Box, FakeService, tk_root, ui  # noqa: E402,F401
from pc import ui as appmod  # noqa: E402
from pc import table as T  # noqa: E402


class FakeWin:
    def __init__(self, autostart=False, awake_ok=True):
        self.autostart, self.awake_ok = autostart, awake_ok
        self.awake: list = []
        self.fail_autostart = False

    def autostart_enabled(self):
        return self.autostart

    def set_autostart(self, on):
        if self.fail_autostart:
            raise OSError("denied")
        self.autostart = on
        return on

    def set_keep_awake(self, on):
        self.awake.append(on)
        return self.awake_ok


def make_app(tk_root, tmp_path, win=None, **kw):
    for w in tk_root.winfo_children():
        w.destroy()
    box = Box()
    app = appmod.App(tk_root, FakeService(), tmp_path, open_url=lambda u: None, msgbox=box,
                     inline=True, win=win or FakeWin(), **kw)
    app.box = box
    app.refresh_rows()
    app.update_status()
    return app


def test_settings_options_default_off_and_reflect_the_registry(tk_root, tmp_path):
    app = make_app(tk_root, tmp_path)
    assert app.autostart_var.get() is False and app.keep_awake_var.get() is False
    app.close(destroy=False)
    app2 = make_app(tk_root, tmp_path, FakeWin(autostart=True))
    assert app2.autostart_var.get() is True
    app2.win.autostart = False          # changed outside (Task Manager): the menu re-reads it
    app2._sync_autostart()
    assert app2.autostart_var.get() is False
    app2.close(destroy=False)


def test_autostart_toggle_calls_the_registry_and_failure_reverts_with_a_message(tk_root, tmp_path):
    app = make_app(tk_root, tmp_path)
    app.autostart_var.set(True)
    app._autostart_toggled()
    assert app.win.autostart is True and app.autostart_var.get() is True
    app.autostart_var.set(False)
    app._autostart_toggled()
    assert app.win.autostart is False and app.autostart_var.get() is False
    app.win.fail_autostart = True
    app.autostart_var.set(True)
    app._autostart_toggled()
    assert app.autostart_var.get() is False and app.box.shown[-1][0] == "error"
    app.close(destroy=False)


def test_keep_awake_toggle_persists_applies_and_is_released_on_exit(tk_root, tmp_path):
    app = make_app(tk_root, tmp_path)
    app.keep_awake_var.set(True)
    app._keep_awake_toggled()
    assert app.win.awake == [True] and app.keep_awake is True
    assert json.loads((tmp_path / "ui.json").read_text(encoding="utf-8"))["keep_awake"] is True
    app.keep_awake_var.set(False)
    app._keep_awake_toggled()
    assert app.win.awake == [True, False]
    assert json.loads((tmp_path / "ui.json").read_text(encoding="utf-8"))["keep_awake"] is False
    app.keep_awake_var.set(True)
    app._keep_awake_toggled()
    app.close(destroy=False)
    assert app.win.awake == [True, False, True, False]      # reset when the app exits


def test_keep_awake_refused_by_windows_reverts_the_option(tk_root, tmp_path):
    app = make_app(tk_root, tmp_path, FakeWin(awake_ok=False))
    app.keep_awake_var.set(True)
    app._keep_awake_toggled()
    assert app.keep_awake_var.get() is False and app.keep_awake is False
    assert app.box.shown[-1][0] == "error"
    app.close(destroy=False)


def test_saved_keep_awake_is_restored_on_the_next_start(tk_root, tmp_path):
    (tmp_path / "ui.json").write_text(json.dumps({"keep_awake": True}), encoding="utf-8")
    app = make_app(tk_root, tmp_path)
    assert app.keep_awake is True and app.keep_awake_var.get() is True
    assert app.win.awake == []          # applied by start(), never while only building
    app.close(destroy=False)


def test_status_block_is_in_the_sidebar_and_holds_the_buttons(ui):
    assert ui.status.master is ui.sidebar and ui.notebook.master is ui.shell
    assert ui.refresh_btn.master is ui.pause_btn.master is ui.actions
    assert ui.actions.master is ui.status
    assert set(ui.status_labels) == {"last", "mode", "next", "gh", "hb", "pending"}
    assert ui.status_labels["gh"].cget("text").startswith("sağlıklı")


def test_followed_rows_are_not_bold_only_new_items_are_purple_bold(ui):
    t = ui.tree
    by_slug = {r.slug: r for r in ui.rows}
    followed = by_slug["p1"]
    assert followed.watched and "watched" in t.item("p1", "tags")
    font, fg, _ = t._row_style(t._rows[t._index["p1"]], "title", t.cell("p1", "title"))
    assert font.actual("weight") == "normal"
    ui.svc._state["plays"]["p3"] = {
        "title": "Üçüncü Oyun", "url": "https://example.test/p3", "tags": [], "genre": "Dram",
        "sessions": {"z": {"start": "2099-01-01T20:00:00+03:00", "status": "on_sale"}}}
    ui.svc.new_slugs = set()
    ui.refresh_rows()                      # after the baseline: p3 is unseen = new
    new = [r for r in ui.rows if r.is_new]
    assert [r.slug for r in new] == ["p3"]
    nfont, nfg, _ = t._row_style(t._rows[t._index[new[0].slug]], "title",
                                 t.cell(new[0].slug, "title"))
    assert nfont.actual("weight") == "bold" and nfg == appmod.NEW_COLOR
    # the colour/bold is limited to the name, not the other cells
    ofont, _ofg, _ = t._row_style(t._rows[t._index[new[0].slug]], "genre",
                                  t.cell(new[0].slug, "genre"))
    assert ofont.actual("weight") == "normal"


def make_table(tk_root, **kw):
    frame = tk.Frame(tk_root)
    cols = (("follow", "Takip", 50), ("b", "B", 120), ("link", "Sayfa", 60))
    table = T.GridTable(frame, cols, stretch=("b",), **kw)
    table.pack(fill="both", expand=True)
    frame.pack(fill="both", expand=True)
    return frame, table


def rects(table):
    body = table.body
    return [(body.coords(i), body.itemcget(i, "fill"), body.itemcget(i, "outline"))
            for i in body.find_all() if body.type(i) == "rectangle"]


def test_zebra_bands_alternate_faintly_and_never_cover_selection_or_status_colours(tk_root):
    frame, t = make_table(tk_root)
    t.tag_configure("removed", background="#fde2e2")
    for n in range(6):
        t.insert("", "end", iid=f"r{n}", cells=[L.Cell("☐", False, "check"), L.Cell("x"),
                                                L.Cell("—")],
                 tags=("removed",) if n == 3 else ())
    t.selection_set("r1")
    tk_root.update()
    t.draw()
    fills = {}
    for coords, fill, _o in rects(t):
        if fill and coords[0] == 0:
            fills[int(coords[1]) // t.row_px] = fill
    assert fills[1] == T.SELECT_BG                   # selection wins over the band
    assert fills[3] == "#fde2e2"                     # row colour wins over the band
    assert fills[5] == T.ZEBRA_BG and 0 not in fills and 2 not in fills and 4 not in fills
    r, g, b = (int(T.ZEBRA_BG[i:i + 2], 16) for i in (1, 3, 5))
    assert min(r, g, b) >= 0xF4                      # barely visible
    frame.destroy()


def test_zebra_can_be_switched_off(tk_root):
    frame, t = make_table(tk_root, zebra=False)
    for n in range(3):
        t.insert("", "end", iid=f"r{n}", cells=[L.Cell("☐", False, "check"), L.Cell("x"),
                                                L.Cell("—")])
    tk_root.update()
    t.draw()
    assert all(fill != T.ZEBRA_BG for _c, fill, _o in rects(t))
    frame.destroy()


def test_checkbox_is_drawn_to_the_row_height_and_links_are_plain_text(tk_root):
    frame, t = make_table(tk_root)
    t.insert("", "end", iid="on", cells=[L.Cell("☑", False, "check"), L.Cell("x"),
                                         L.Cell("Oyun ↗", False, "link")])
    t.insert("", "end", iid="off", cells=[L.Cell("☐", False, "check"), L.Cell("y"),
                                          L.Cell("Seans ↗", False, "link")])
    tk_root.update()
    t.draw()
    boxes = [(c, f, o) for c, f, o in rects(t) if o in (T.BOX_ON, T.BOX_EDGE)]
    assert [f for _c, f, _o in boxes] == [T.BOX_ON, "white"]
    for coords, _f, _o in boxes:                     # square, inside its row, ~55 % of height
        w, h = coords[2] - coords[0], coords[3] - coords[1]
        assert abs(w - h) <= 1 and 0.4 * t.row_px <= h <= 0.7 * t.row_px
    texts = {t.body.itemcget(i, "text"): t.body.itemcget(i, "fill")
             for i in t.body.find_all() if t.body.type(i) == "text"}
    assert "☑" not in texts and "☐" not in texts      # no font glyph
    assert texts["Oyun ↗"] == texts["Seans ↗"] == T.LINK_COLOR
    # no bordered button look around the link cells
    assert not [r for r in rects(t) if r[2] == "#9aa7b8" or r[1] == "#eef2f7"]
    frame.destroy()


def test_link_is_underlined_only_while_hovered_and_cells_stay_clickable(tk_root):
    frame, t = make_table(tk_root)
    clicks = []
    t._on_cell = lambda iid, key: clicks.append((iid, key))
    t.insert("", "end", iid="a", cells=[L.Cell("☐", False, "check"), L.Cell("x"),
                                        L.Cell("Oyun ↗", False, "link")])
    tk_root.update()
    t.draw()

    def link_font():
        item = [i for i in t.body.find_all() if t.body.type(i) == "text"
                and t.body.itemcget(i, "text") == "Oyun ↗"][0]
        return tkfont.Font(font=t.body.itemcget(item, "font")).actual("underline")
    assert link_font() == 0
    _w, xs = t._layout()
    ev = type("E", (), {"x": (xs[2] + xs[3]) // 2, "y": t.row_px // 2})
    t._motion(ev)
    t.draw()
    assert link_font() == 1
    t._leave(None)
    t.draw()
    assert link_font() == 0
    t._click(ev)
    t._click(type("E", (), {"x": (xs[0] + xs[1]) // 2, "y": t.row_px // 2}))
    assert clicks == [("a", "link"), ("a", "follow")]
    assert (xs[3] - xs[2]) >= 50 and t.row_px >= 22     # adequate hit area
    frame.destroy()


def test_table_metrics_scale_with_the_dpi_but_not_below_the_font(tk_root):
    frame, t = make_table(tk_root, row_px=22, head_px=26)
    assert t.scale >= 1.0 and t.row_px >= 22 and t.head_px >= 26
    old = tk_root.tk.call("tk", "scaling")
    try:
        tk_root.tk.call("tk", "scaling", 2.0 * 96 / 72)       # as 200 % DPI
        frame2, big = make_table(tk_root, row_px=22, head_px=26)
        assert big.scale == pytest.approx(2.0, rel=0.02)
        assert big.row_px >= 44 and big.head_px >= 52
        assert sum(big._base.values()) >= 2 * 230 - 4
        frame2.destroy()
    finally:
        tk_root.tk.call("tk", "scaling", old)
    frame.destroy()


def test_legend_box_is_titled_and_shows_real_samples(ui):
    titles = [w.cget("text") for w in ui.legend.winfo_children() if w.winfo_class() == "Label"]
    assert titles[0] == "Açıklama"
    assert ui.legend_texts[:3] == ["Normal yazı = kaynaktan alınan",
                                   "İtalik = türetilmiş / varsayım",
                                   "Mor kalın = yeni, henüz açılmadı"]
    assert [t.split(" ")[0] for t in ui.legend_texts[3:7]] == ["✅", "⚠️", "❌", "⚪"]
    assert not hasattr(ui, "legend_label")
    assert ui.legend.master is ui.sidebar         # wave 12: in the sidebar, not above the list
# ---- source cards ------------------------------------------------------------------------------

def _records(now, fail_vkv=True):
    st = {}
    S.record_check(st, "biletinial", now - timedelta(minutes=3), True, items=[1] * 6)
    S.record_check(st, "basiskele", now - timedelta(minutes=45), True, items=[1] * 5)
    S.record_check(st, "vkv", now - timedelta(minutes=2), not fail_vkv, items=[1],
                   error="Zaman aşımı" if fail_vkv else None)
    return st


def test_source_card_texts_and_colours_for_every_state():
    st = _records(NOW)
    views = {v["id"]: v for v in S.describe(st, NOW + timedelta(minutes=1))}
    ok = L.source_card(views["biletinial"], NOW + timedelta(minutes=1))
    assert (ok.status, ok.color, ok.icon, ok.title) == ("ok", L.GREEN, "✅", "Biletinial")
    assert ok.lines == ("Son kontrol: 11:57 (4 dk önce)", "Son güncelleme: bugün 11:57",
                        "6 etkinlik")
    late = L.source_card(views["basiskele"], NOW + timedelta(minutes=31))
    assert late.status == "stale" and late.color == L.AMBER and late.icon == "⚠️"
    assert late.lines[0] == "Son kontrol: 11:15 (1 sa önce)" and late.lines[2] == "5 etkinlik"
    bad = L.source_card(views["vkv"], NOW + timedelta(minutes=1))
    assert bad.status == "failed" and bad.color == L.RED and bad.icon == "❌"
    assert bad.error == "Zaman aşımı"
    unknown = L.source_card({"id": "x", "name": "X", "status": "unknown", "icon": "⚪",
                             "last_check": "—", "last_change": "—", "items_count": None,
                             "error": None}, NOW)
    assert unknown.color == L.GRAY and unknown.lines == (
        "Son kontrol: —", "Son güncelleme: —", "— etkinlik")


def test_source_card_time_phrases():
    base = {"id": "s", "name": "S", "status": "ok", "icon": "✅", "items_count": 2,
            "last_check": "", "last_change": ""}
    def card(check_ago, change_ago):
        v = dict(base, last_check_at=(NOW - check_ago).isoformat(),
                 last_change_at=(NOW - change_ago).isoformat())
        return L.source_card(v, NOW).lines
    assert card(timedelta(seconds=20), timedelta(hours=26))[0] == "Son kontrol: 11:59 (az önce)"
    assert card(timedelta(seconds=20), timedelta(hours=26))[1] == "Son güncelleme: dün 10:00"
    assert card(timedelta(minutes=12), timedelta(days=3))[0].endswith("(12 dk önce)")
    assert card(timedelta(minutes=12), timedelta(days=3))[1] == "Son güncelleme: 12.11 12:00"
    assert card(timedelta(hours=5), timedelta(hours=5))[0].endswith("(5 sa önce)")


def test_portal_line_is_added_for_basiskele_only():
    views = L.source_views_with_portals(
        S.describe(_records(NOW), NOW), [type("P", (), {
            "id": "basiskele", "state": "empty", "text": "x", "url": "https://p.test"})()])
    cards = {v["id"]: L.source_card(v, NOW) for v in views}
    assert cards["basiskele"].lines[-1] == "Rezervasyon portalı: boş"
    assert all("portalı" not in " ".join(c.lines) for k, c in cards.items() if k != "basiskele")


def _three_sources(ui, fail_vkv=True):
    from pc.viewmodel import PortalView
    st = _records(datetime.now(ISTANBUL), fail_vkv)
    ui.svc.sources = lambda now=None: S.describe(st, datetime.now(ISTANBUL))
    ui.svc.portals = lambda now=None: [PortalView("basiskele", "x", "https://portal.test",
                                                  "empty")]
    ui.svc.config.sources = ["https://biletinial.test/mekan/x"]
    ui._source_sig = None
    ui.refresh_rows()


def _show(ui, width, height=800):
    root = ui.root
    root.deiconify()
    root.geometry(f"{width}x{height}+0+0")
    for _ in range(6):
        root.update()
    ui._fit_panes()
    for _ in range(4):
        root.update()


def rect(w):
    return (w.winfo_rootx(), w.winfo_rooty(), w.winfo_rootx() + w.winfo_width(),
            w.winfo_rooty() + w.winfo_height())


def inside(inner, outer):
    return (inner[0] >= outer[0] and inner[2] <= outer[2] and inner[1] >= outer[1]
            and inner[3] <= outer[3])


def test_three_cards_stacked_in_the_sidebar_with_buttons_and_no_clipping(ui):
    _three_sources(ui)
    _show(ui, 1400)
    try:
        assert list(ui.source_cards) == ["biletinial", "basiskele", "vkv"]
        frames = [ui.source_cards[k]["frame"] for k in ui.source_cards]
        assert len({f.winfo_rootx() for f in frames}) == 1              # one column
        ys = [f.winfo_rooty() for f in frames]
        assert ys == sorted(ys) and len(set(ys)) == 3                   # stacked, in order
        for f in frames:
            assert inside(rect(f), rect(ui.source_bar))
        for key, card in ui.source_cards.items():
            for part in ("title", "body", "icon", "links"):
                assert inside(rect(card[part]), rect(card["frame"])), (key, part)
            for part in ("title", "body"):                  # text fits: nothing clipped
                lab = card[part]
                assert lab.winfo_height() >= lab.winfo_reqheight(), (key, part)
                assert lab.winfo_width() >= lab.winfo_reqwidth(), (key, part)
        assert set(ui.source_open_buttons) == {"biletinial", "basiskele", "vkv"}
        assert [b.cget("text") for b in ui.source_buttons] == ["Portalı aç"]
        assert ui.source_cards["vkv"]["error"].winfo_ismapped()          # failed card
        assert not ui.source_cards["biletinial"]["error"].winfo_ismapped()
        blocks = [rect(ui.status), rect(ui.source_bar), rect(ui.legend)]   # no overlaps
        for a, b in zip(blocks, blocks[1:]):
            assert a[3] <= b[1] + 1
        assert rect(ui.sidebar)[2] <= rect(ui.notebook)[0] + 1            # sidebar left of tabs
    finally:
        ui.root.withdraw()


def test_narrow_window_turns_the_sidebar_into_a_top_strip(ui):
    _three_sources(ui)
    _show(ui, 760)
    try:
        assert ui.narrow is True
        assert rect(ui.side_wrap)[3] <= rect(ui.notebook)[1] + 1       # strip above the tabs
        assert rect(ui.side_wrap)[2] - rect(ui.side_wrap)[0] >= 740      # full width
        frames = [ui.source_cards[k]["frame"] for k in ui.source_cards]
        for f in frames:
            assert inside(rect(f), rect(ui.source_bar))
        assert inside(rect(ui.source_bar), rect(ui.sidebar))
    finally:
        ui.root.withdraw()


def test_cards_update_in_place_and_colour_follows_the_status(ui):
    _three_sources(ui)
    first = ui.source_labels[0]
    ui.refresh_rows()
    assert ui.source_labels[0] is first
    assert ui.source_cards["vkv"]["icon"].cget("text") == "❌"
    assert str(ui.source_cards["vkv"]["icon"].cget("foreground")) == L.RED
    assert str(ui.source_cards["vkv"]["frame"].cget("highlightbackground")) == L.RED
    _three_sources(ui, fail_vkv=False)
    assert ui.source_cards["vkv"]["icon"].cget("text") == "✅"
    assert str(ui.source_cards["vkv"]["frame"].cget("highlightbackground")) == L.GREEN


def test_refresh_and_pause_buttons_are_kept(ui):
    assert ui.refresh_btn.cget("text") == "Listeyi güncelle"
    assert ui.pause_btn.cget("text") == "Duraklat"


def test_sources_describe_exposes_raw_timestamps_for_the_cards():
    v = S.describe(_records(NOW), NOW)[0]
    assert v["last_check_at"] and v["last_change_at"]
