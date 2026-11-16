"""Wave 18 C: the alert popup is a plain native window with one generic line (no names, dates,
links), one popup for alerts that arrive together, never takes the focus, closes itself."""
from __future__ import annotations

import sys

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import ttk  # noqa: E402

from pc import app_logic as L  # noqa: E402
from pc import ui as appmod  # noqa: E402
from pc import winsetup  # noqa: E402
from tests.test_pc_app_smoke import Box, FakeService, tk_root, ui  # noqa: E402,F401
from tiyatro.models import Event  # noqa: E402

SECRET = "Çok Gizli Oyun Adı"


def _ev(kind, key, **payload):
    return Event(kind, key, "p1", "a", ["y"], {"title": SECRET, "url": "https://secret.example/x",
                                              **payload})


def _fire(ui, events_by_call):
    for cb in ui.svc.callbacks:
        for events in events_by_call:
            cb(f"{SECRET} — 12 Kasım 20:00", f"{SECRET}\nhttps://secret.example/x\n4 kişi", events)
    ui.pump()


def _all_text(widget):
    out = []
    for w in [widget, *widget.winfo_children()]:
        for opt in ("text",):
            try:
                out.append(str(w.cget(opt)))
            except tk.TclError:
                pass
        out.extend(_all_text(c) for c in w.winfo_children() if False)
    for c in widget.winfo_children():
        out.extend(_all_text(c))
    return out


def test_popup_shows_one_generic_line_and_nothing_else(ui):
    pass
    _fire(ui, [[_ev("ON_SALE", "k1")]])
    assert ui.popup is None                      # collected first, not shown per event
    ui.flush_popups()
    pop = ui.popup
    pop.update()
    assert pop.winfo_ismapped()
    texts = " | ".join(_all_text(pop)) + " | " + pop.title()
    assert pop.label.cget("text") == "Tiyatro Takip: bilet açıldı"
    for forbidden in (SECRET, "secret.example", "https", "12 Kasım", "kişi", "20:00"):
        assert forbidden not in texts
    pop.dismiss()


def test_alerts_of_one_scan_become_one_popup_with_the_strongest_wording(ui):
    ui.popup_coalesce_ms = 150
    _fire(ui, [[_ev("SRC_NEW", "a")], [_ev("SRC_OPENED", "b")], [_ev("SRC_NEW", "a")],
               [_ev("ON_SALE", "c")], [_ev("NEW_PLAY", "d")]])
    for _ in range(40):                         # the real timer fires once
        ui.root.update()
        ui.root.after(10)
        if ui.popup is not None:
            break
    assert ui.popup is not None
    assert len([w for w in ui.root.winfo_children() if isinstance(w, appmod.Popup)]) == 1
    assert ui.popup.label.cget("text") == (
        "Tiyatro Takip: 4 yeni gelişme\nEn önemlisi: bilet açıldı")      # a, b, c, d (a twice = once)
    # later alerts while it is open refresh the SAME popup
    first = ui.popup
    _fire(ui, [[_ev("SRC_NEW", "e")]])
    ui.flush_popups()
    assert ui.popup is first and "5 yeni gelişme" in first.label.cget("text")
    first.dismiss()
    assert ui.popup is None
    _fire(ui, [[_ev("SRC_NEW", "f")]])           # after closing: a new, fresh popup
    ui.flush_popups()
    assert ui.popup is not first and ui.popup.label.cget("text") == "Tiyatro Takip: yeni etkinlik"
    ui.popup.dismiss()


def test_popup_uses_native_widgets_and_a_normal_window_frame(ui):
    _fire(ui, [[_ev("SRC_OPENED", "k")]])
    ui.flush_popups()
    pop = ui.popup
    pop.update()

    def classes(w):
        out = {w.winfo_class()}
        for c in w.winfo_children():
            out |= classes(c)
        return out
    assert classes(pop) <= {"Toplevel", "TFrame", "TLabel", "TButton"}
    assert not pop.overrideredirect()
    assert pop.attributes("-topmost")
    assert pop.title() == "Tiyatro Takip" and pop.resizable() == (False, False)
    assert pop.open_btn.cget("text") == "Aç"
    assert pop.protocol("WM_DELETE_WINDOW")           # the native close (X) button
    assert str(pop.label.cget("font")) in ("", "TkDefaultFont")   # system UI font, nothing custom
    if sys.platform == "win32":
        assert pop.attributes("-toolwindow") == 0
    pop.dismiss()


def test_popup_is_placed_bottom_right_inside_the_work_area(ui):
    _fire(ui, [[_ev("ON_SALE", "k")]])
    ui.flush_popups()
    pop = ui.popup
    pop.update()
    left, top, right, bottom = winsetup.work_area(ui.root)
    assert left <= pop.winfo_x() and pop.winfo_x() + pop.winfo_width() <= right
    assert top <= pop.winfo_y() and pop.winfo_y() + pop.winfo_height() <= bottom
    assert right - (pop.winfo_x() + pop.winfo_width()) < ui.px(60)
    assert bottom - (pop.winfo_y() + pop.winfo_height()) < ui.px(80)
    pop.dismiss()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows extended window style")
def test_popup_never_activates():
    import ctypes
    root = tk.Tk()
    try:
        top = tk.Toplevel(root)
        assert winsetup.set_no_activate(top)
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(top.winfo_id()) or top.winfo_id()
        assert user32.GetWindowLongW(hwnd, -20) & winsetup.NO_ACTIVATE
    finally:
        root.destroy()


def test_click_or_open_button_raises_the_main_window_and_closes_the_popup(ui):
    raised = []
    ui.show_main = lambda: raised.append(1)
    for how in ("button", "click"):
        _fire(ui, [[_ev("ON_SALE", how)]])
        ui.flush_popups()
        pop = ui.popup
        pop.update()
        if how == "button":
            pop.open_btn.invoke()
        else:
            pop.label.event_generate("<Button-1>", x=3, y=3)
        assert ui.popup is None, how
    assert raised == [1, 1]
    assert ui._beep_after is None                      # seen: beeps stopped


def test_timeout_closes_the_popup_but_the_beep_series_keeps_its_course(ui):
    _fire(ui, [[_ev("ON_SALE", "k")]])
    ui.flush_popups()
    assert len(ui.beeps_log) == 1 and ui._beep_after is not None
    ui.popup.dismiss(user=False)                       # what the timer does
    assert ui.popup is None and ui._beep_after is not None
    ui._stop_beeps()
    assert L.POPUP_SECONDS == 30


def test_beep_setting_is_still_respected(ui):
    ui.beep_enabled = False
    _fire(ui, [[_ev("ON_SALE", "k")]])
    ui.flush_popups()
    assert ui.beeps_log == []
    ui.popup.dismiss()


def test_real_screenshot_of_the_popup(ui, tmp_path):
    """Renders the popup in a real mapped window and writes a PNG-less raw check: the window has
    its native frame (non-client area) and the right size."""
    _fire(ui, [[_ev("ON_SALE", "k")]])
    ui.flush_popups()
    pop = ui.popup
    pop.update()
    assert pop.winfo_width() < ui.px(400) and pop.winfo_height() < ui.px(200)
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(pop.winfo_id())
        outer, inner = wintypes.RECT(), pop.winfo_height()
        user32.GetWindowRect(hwnd, ctypes.byref(outer))
        assert outer.bottom - outer.top > inner + ui.px(20)      # title bar + borders around it
    pop.dismiss()


def test_bottom_right_puts_the_outer_rect_inside_the_work_area_with_margin():
    work = (0, 0, 3840, 2112)                  # 4K primary, 48 px taskbar
    outer = (420, 210)                          # frame + title bar included
    x, y = winsetup.bottom_right(work, outer, 16)
    assert x + outer[0] == 3840 - 16 and y + outer[1] == 2112 - 16
    # second monitor to the right: its own work area, not the primary one
    x, y = winsetup.bottom_right((3840, 0, 5760, 1032), outer, 16)
    assert x + outer[0] == 5760 - 16 and y + outer[1] == 1032 - 16 and x >= 3840
    # tiny work area: never above/left of the origin
    assert winsetup.bottom_right((0, 0, 100, 100), outer, 16) == (0, 0)


def test_popup_uses_the_stubbed_work_area_and_the_real_outer_height(ui, monkeypatch):
    monkeypatch.setattr(winsetup, "work_area", lambda root=None, user32=None: (200, 100, 1200, 800))
    _fire(ui, [[_ev("ON_SALE", "k")]])
    ui.flush_popups()
    pop = ui.popup
    pop.update()
    ow, oh = winsetup.outer_size(pop)
    assert oh > pop.winfo_height() or sys.platform != "win32"   # title bar + frame counted
    x, y = winsetup.bottom_right((200, 100, 1200, 800), (ow, oh), ui.px(16))
    pop.place()
    pop.update()
    assert (pop.winfo_x(), pop.winfo_y()) == (x, y)
    assert x + ow <= 1200 - ui.px(16) and y + oh <= 800 - ui.px(16)
    pop.dismiss()


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 GetWindowRect / monitor work area")
def test_real_popup_outer_rect_is_above_the_taskbar(ui):
    import ctypes
    from ctypes import wintypes
    _fire(ui, [[_ev("ON_SALE", "k")]])
    ui.flush_popups()
    pop = ui.popup
    pop.update()
    rect = wintypes.RECT()
    user32 = ctypes.windll.user32
    assert user32.GetWindowRect(user32.GetParent(pop.winfo_id()) or pop.winfo_id(), ctypes.byref(rect))
    left, top, right, bottom = winsetup.work_area(ui.root)
    print("work", (left, top, right, bottom), "rect", (rect.left, rect.top, rect.right, rect.bottom))
    margin = ui.px(16)
    assert rect.bottom <= bottom - margin + 1 and rect.right <= right - margin + 1
    assert rect.left >= left and rect.top >= top
    pop.dismiss()
