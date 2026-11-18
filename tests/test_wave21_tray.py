"""Wave 21: minimise -> system tray. Win32 calls are stubbed here; the real check is
``test_real_tray_roundtrip`` (Windows only) plus the manual evidence in the wave report."""
from __future__ import annotations

import json
import sys

import pytest

tk = pytest.importorskip("tkinter")

from pc import app_logic as L  # noqa: E402
from pc import tray as T  # noqa: E402
from pc import winsetup as W  # noqa: E402
from tests.test_pc_app_smoke import Box, FakeService, tk_root, ui  # noqa: E402,F401


class FakeApi:
    taskbar_created = 0xC123

    def __init__(self, ok=True):
        self.calls, self.ok = [], ok
        self.handler = None

    def create_window(self, handler):
        self.handler = handler
        self.calls.append("create")
        return 99

    def destroy_window(self, hwnd):
        self.calls.append(("destroy", hwnd))

    def notify(self, action, hwnd, ico, tip):
        self.calls.append(("notify", action, tip))
        return self.ok

    def cursor_pos(self):
        return (10, 20)

    def track_menu(self, hwnd, items, pos):
        self.calls.append(("menu", [i[1] for i in items], pos))
        return "pause"


class FakeTray:
    ok = True
    choice = None

    def __init__(self, ico, on_event, schedule):
        self.on_event, self.schedule = on_event, schedule
        self.present, self.log = False, []

    def add(self):
        self.log.append("add")
        self.present = self.ok
        return self.ok

    def remove(self):
        self.log.append("remove")
        self.present = False

    def destroy(self):
        self.log.append("destroy")
        self.present = False

    def popup_menu(self, items, pos):
        self.log.append(("menu", [i[1] for i in items]))
        return self.choice


class FailingTray(FakeTray):
    ok = False


# ---- pure logic ------------------------------------------------------------------------------
def test_menu_labels_and_pause_label():
    assert T.menu_items(False) == [("open", "Aç"), ("refresh", "Listeyi güncelle"),
                                   ("pause", "Duraklat"), (None, ""), ("quit", "Çıkış")]
    assert dict(T.menu_items(True))["pause"] == "Devam"


def test_message_mapping():
    assert T.event_for_message(T.WM_TRAY, T.WM_LBUTTONUP) == "restore"
    assert T.event_for_message(T.WM_TRAY, T.WM_LBUTTONDBLCLK) == "restore"
    assert T.event_for_message(T.WM_TRAY, T.WM_RBUTTONUP) == "menu"
    assert T.event_for_message(T.WM_TRAY, 0x0200) is None        # mouse move
    assert T.event_for_message(0x0001, T.WM_LBUTTONUP) is None   # not our callback message


def test_trayicon_add_remove_idempotent_and_events_scheduled():
    api, scheduled, events = FakeApi(), [], []
    icon = T.TrayIcon("x.ico", lambda n, p: events.append((n, p)),
                      lambda ms, fn: scheduled.append(fn), api=api)
    assert icon.add() and icon.add()
    assert api.calls.count(("notify", T.NIM_ADD, "Tiyatro Takip")) == 1
    assert api.handler(T.WM_TRAY, 1, T.WM_LBUTTONUP) is True
    assert api.handler(T.WM_TRAY, 1, T.WM_RBUTTONUP) is True
    assert api.handler(T.WM_RESTORE, 0, 0) is True
    assert api.handler(0x0001, 0, 0) is False
    assert events == []                      # nothing runs inside the window procedure
    assert len(scheduled) == 1               # one poll scheduled when the icon was added
    scheduled.pop()()
    assert len(scheduled) == 1               # polling continues while the icon is shown
    assert events == [("restore", None), ("menu", (10, 20)), ("restore", None)]
    api.calls.clear()                        # Explorer restart: the icon is added again
    assert api.handler(api.taskbar_created, 0, 0) is True
    assert ("notify", T.NIM_ADD, "Tiyatro Takip") in api.calls
    icon.remove()
    icon.remove()
    assert [c for c in api.calls if c[0] == "notify" and c[1] == T.NIM_DELETE] == \
        [("notify", T.NIM_DELETE, "Tiyatro Takip")]
    icon.destroy()
    assert ("destroy", 99) in api.calls


def test_trayicon_add_failure_reports_false():
    icon = T.TrayIcon("x.ico", lambda *a: None, lambda ms, f: None, api=FakeApi(ok=False))
    assert icon.add() is False and icon.present is False


def test_load_ui_default_on_and_roundtrip(tmp_path):
    assert L.load_ui(tmp_path / "none.json")["minimize_to_tray"] is True
    p = tmp_path / "ui.json"
    p.write_text(json.dumps({"minimize_to_tray": False}), encoding="utf-8")
    assert L.load_ui(p)["minimize_to_tray"] is False
    p.write_text(json.dumps({"minimize_to_tray": "no"}), encoding="utf-8")
    assert L.load_ui(p)["minimize_to_tray"] is True


# ---- App state machine (stub tray, real Tk root) ------------------------------------------------
def _app(ui, tray_on=True, factory=FakeTray):
    ui.tray_factory = factory
    ui.minimize_to_tray = tray_on
    ui.root.deiconify()
    ui.root.update()
    return ui


def _minimise(ui):
    ui.root.iconify()
    ui.root.update()
    ui.root.update()


def test_minimise_goes_to_tray_and_restore_comes_back(ui):
    _app(ui)
    _minimise(ui)
    assert ui.in_tray and ui.root.state() == "withdrawn"
    assert ui.tray.log == ["add"] and ui.tray.present
    ui._tray_event("restore", None)
    ui.root.update()
    assert not ui.in_tray and ui.root.state() == "normal" and not ui.tray.present


def test_option_off_keeps_taskbar_behaviour(ui):
    _app(ui, tray_on=False)
    _minimise(ui)
    assert not ui.in_tray and ui.root.state() == "iconic" and ui.tray is None


def test_settings_checkbox_saved_in_ui_json(ui):
    ui.tray_var.set(False)
    ui._tray_toggled()
    assert ui.minimize_to_tray is False
    assert L.load_ui(ui.local_dir / "ui.json")["minimize_to_tray"] is False


def test_menu_commands(ui):
    _app(ui)
    _minimise(ui)
    called = []
    ui.toggle_pause = lambda: called.append("pause")
    ui.refresh_list = lambda: called.append("refresh")
    ui.tray_command("pause")
    ui.tray_command("refresh")
    assert called == ["pause", "refresh"]
    ui.tray_command("open")
    assert not ui.in_tray and ui.root.state() == "normal"
    ui.close(destroy=False)
    assert ui.closing is True


def test_menu_event_runs_chosen_action(ui):
    _app(ui)
    _minimise(ui)
    ui.tray.choice = "pause"
    called = []
    ui.toggle_pause = lambda: called.append("pause")
    ui._tray_event("menu", (1, 2))
    assert called == ["pause"] and ("menu", ["Aç", "Listeyi güncelle", ui.tray_menu()[2][1], "", "Çıkış"]) \
        in ui.tray.log


def test_close_removes_icon_and_popup_open_restores(ui):
    _app(ui)
    _minimise(ui)
    ui.show_main()                      # what the popup's "Aç" button calls
    assert not ui.in_tray and not ui.tray.present
    _minimise(ui)
    assert ui.in_tray
    tray = ui.tray
    ui.close(destroy=False)
    assert "destroy" in tray.log and not tray.present


def test_tray_failure_leaves_window_on_taskbar(ui):
    _app(ui, factory=FailingTray)
    _minimise(ui)
    assert not ui.in_tray and ui.root.state() == "iconic"


# ---- second launch ---------------------------------------------------------------------------------
class U32:
    def __init__(self, visible, iconic=False):
        self.visible, self.iconic, self.calls = visible, iconic, []

    def FindWindowW(self, c, t):
        return 5

    def IsWindowVisible(self, h):
        return self.visible

    def IsIconic(self, h):
        return self.iconic

    def ShowWindow(self, h, cmd):
        self.calls.append(("show", cmd))

    def SetForegroundWindow(self, h):
        self.calls.append("fg")


def test_second_launch_with_window_in_tray_posts_restore():
    api, posted = U32(visible=False), []
    assert W.focus_existing_window("Tiyatro Takip", api, post_restore=lambda: posted.append(1) or True)
    assert posted == [1] and api.calls == []       # Tk restores itself; no raw ShowWindow


def test_second_launch_visible_window_unchanged():
    api = U32(visible=True)
    assert W.focus_existing_window("Tiyatro Takip", api, post_restore=lambda: 1 / 0)
    assert ("show", 5) in api.calls


# ---- REAL Win32 (this machine) ------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_real_tray_roundtrip(ui):
    import ctypes
    u32 = ctypes.windll.user32
    u32.GetParent.restype = ctypes.c_void_p
    u32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    u32.FindWindowExW.restype = ctypes.c_void_p
    u32.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
    ui.tray_factory = T.TrayIcon
    ui.minimize_to_tray = True
    ui.root.deiconify()
    ui.root.update()
    hwnd = u32.GetParent(ui.root.winfo_id()) or ui.root.winfo_id()
    assert u32.IsWindowVisible(hwnd)
    _minimise(ui)
    assert ui.in_tray and ui.tray.present
    assert not u32.IsWindowVisible(hwnd)
    assert u32.FindWindowExW(ctypes.c_void_p(-3), None, T.CLASS_NAME, None)  # tray window exists
    assert T.post_restore() is True
    import time
    end = time.time() + 3
    while ui.in_tray and time.time() < end:   # message -> deque -> Tk poll -> restore
        ui.root.update()
        time.sleep(0.02)
    assert not ui.in_tray and u32.IsWindowVisible(hwnd) and ui.root.state() == "normal"
    assert not ui.tray.present
    ui.close(destroy=False)
    assert ui.tray.hwnd == 0
