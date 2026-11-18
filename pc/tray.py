"""System tray (notification area) icon of the PC app (wave 21). Stdlib ``ctypes`` only.

Why not pystray (what bot1-hunter / startup-tracker use): pystray + Pillow are installed in the
global Python 3.12, not in this project's ``.venv`` (checked: ``import pystray`` / ``PIL`` fail
there). A new dependency is not wanted, so this is a small ``Shell_NotifyIconW`` wrapper.

Design (no thread, never blocks Tk): a hidden *message-only* window is created on the Tk
thread. Tk's Windows notifier dispatches every message of its thread, so the window procedure
runs inside the normal ``mainloop`` (no second thread, no extra message loop). The procedure
must NOT touch Tk (calling ``after`` from there aborts Python: tkinter has released the GIL
while it dispatches): it only appends a named event (``restore``, ``menu`` + cursor position)
to a deque. While the icon is shown, a cheap Tk ``after`` poll (``POLL_MS``) drains the deque
and runs ``on_event`` as an ordinary Tk callback; the native ``TrackPopupMenu`` is opened there.

Lifetime: ``add()`` -> ``NIM_ADD``; ``remove()`` -> ``NIM_DELETE`` (idempotent, also registered
with ``atexit``); ``destroy()`` removes the icon and the window. If Explorer restarts, the
registered ``TaskbarCreated`` message re-adds the icon. A process that is killed from outside
cannot delete its icon (Windows removes the ghost when the mouse passes over it).
"""
from __future__ import annotations

import atexit
import collections
import os
from pathlib import Path

CLASS_NAME = "TiyatroTakipTrayWnd"
TOOLTIP = "Tiyatro Takip"

POLL_MS = 100
WM_NULL = 0x0000
WM_APP = 0x8000
WM_TRAY = WM_APP + 1            # callback message of the icon (lParam low word = mouse message)
WM_RESTORE = WM_APP + 2         # posted by a second launch of the app: "show the window"
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP, WM_CONTEXTMENU = 0x0202, 0x0203, 0x0205, 0x007B
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4

# menu command id -> action name, in display order; None = separator
OPEN, REFRESH, PAUSE, QUIT = "open", "refresh", "pause", "quit"


def menu_items(paused: bool) -> list[tuple[str | None, str]]:
    """Context menu: ``[(action, label)]``; the pause entry reflects the current state."""
    return [(OPEN, "Aç"), (REFRESH, "Listeyi güncelle"),
            (PAUSE, "Devam" if paused else "Duraklat"), (None, ""), (QUIT, "Çıkış")]


def event_for_message(msg: int, lparam: int) -> str | None:
    """Pure mapping of a tray callback message to an event name (None = ignore)."""
    if msg != WM_TRAY:
        return None
    mouse = lparam & 0xFFFF
    if mouse in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
        return "restore"
    if mouse in (WM_RBUTTONUP, WM_CONTEXTMENU):
        return "menu"
    return None


class TrayIcon:
    """The icon + its hidden window. ``on_event(name, pos)`` runs from the Tk poll (``after(ms,
    fn)`` = ``root.after``) with name in restore | menu and ``pos`` the cursor position (menu)."""

    def __init__(self, ico: Path | str, on_event, after, tooltip: str = TOOLTIP, api=None):
        self.ico, self.on_event, self.after, self.tooltip = str(ico), on_event, after, tooltip
        self.events: collections.deque = collections.deque()   # filled by the window procedure
        self._polling = False
        self.api = api if api is not None else _Win32()
        self.hwnd = 0
        self.present = False          # icon currently registered
        self._atexit = False

    # ---- state -------------------------------------------------------------------------------
    def add(self) -> bool:
        """Create the window (once) and register the icon. True when Windows accepted it."""
        try:
            if not self.hwnd:
                self.hwnd = self.api.create_window(self.handle_message)
            if not self.hwnd:
                return False
            if not self._atexit:
                atexit.register(self.remove)
                self._atexit = True
            if self.present:
                return True
            self.present = bool(self.api.notify(NIM_ADD, self.hwnd, self.ico, self.tooltip))
            if self.present and not self._polling:
                self._polling = True
                self.after(POLL_MS, self.poll)
            return self.present
        except Exception:
            return False

    def remove(self) -> None:
        """``NIM_DELETE``; safe to call any number of times, from any exit path."""
        if self.present:
            self.present = False
            try:
                self.api.notify(NIM_DELETE, self.hwnd, self.ico, self.tooltip)
            except Exception:
                pass

    def destroy(self) -> None:
        self.remove()
        if self.hwnd:
            hwnd, self.hwnd = self.hwnd, 0
            try:
                self.api.destroy_window(hwnd)
            except Exception:
                pass

    def poll(self) -> None:
        """Tk callback: run the events the window procedure queued; keep polling while shown."""
        self._polling = False
        while self.events:
            name, pos = self.events.popleft()
            try:
                self.on_event(name, pos)
            except Exception:
                pass
        if self.present and not self._polling:
            self._polling = True
            self.after(POLL_MS, self.poll)

    # ---- window procedure ---------------------------------------------------------------------
    def handle_message(self, msg: int, wparam: int, lparam: int) -> bool:
        """Called by the window procedure. True = handled."""
        event = event_for_message(msg, lparam)
        if event == "menu":
            self.events.append(("menu", self.api.cursor_pos()))
            return True
        if event == "restore" or msg == WM_RESTORE:
            self.events.append(("restore", None))
            return True
        if msg == self.api.taskbar_created and self.api.taskbar_created:
            if self.present:                 # Explorer restarted: our icon is gone, add it again
                self.present = False
                self.add()
            return True
        return False

    def popup_menu(self, items, pos) -> str | None:
        """Show the native menu at ``pos``; returns the chosen action name or None."""
        if not self.hwnd:
            return None
        return self.api.track_menu(self.hwnd, items, pos)


def post_restore() -> bool:
    """Ask a running instance (its hidden tray window) to show its main window. Used by a
    second launch when the first one lives in the tray. False when no tray window exists."""
    try:
        return _Win32().post_restore()
    except Exception:
        return False


class _Win32:
    """The real Win32 calls. Imported lazily, only on Windows; tests replace the whole class."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        self.ct, self.w = ctypes, w
        u = ctypes.WinDLL("user32", use_last_error=True)
        s = ctypes.WinDLL("shell32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.u, self.s = u, s
        LRESULT = ctypes.c_ssize_t
        self.WNDPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_void_p, ctypes.c_uint,
                                          ctypes.c_size_t, ctypes.c_ssize_t)
        u.DefWindowProcW.restype = LRESULT
        u.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        u.CreateWindowExW.restype = ctypes.c_void_p
        u.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        u.DestroyWindow.argtypes = [ctypes.c_void_p]
        u.RegisterClassW.argtypes = [ctypes.c_void_p]
        u.LoadImageW.restype = ctypes.c_void_p
        u.LoadImageW.argtypes = [ctypes.c_void_p, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]
        u.LoadIconW.restype = ctypes.c_void_p
        u.LoadIconW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        u.DestroyIcon.argtypes = [ctypes.c_void_p]
        u.CreatePopupMenu.restype = ctypes.c_void_p
        u.AppendMenuW.argtypes = [ctypes.c_void_p, w.UINT, ctypes.c_size_t, w.LPCWSTR]
        u.SetMenuDefaultItem.argtypes = [ctypes.c_void_p, w.UINT, w.UINT]
        u.TrackPopupMenu.argtypes = [ctypes.c_void_p, w.UINT, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
        u.DestroyMenu.argtypes = [ctypes.c_void_p]
        u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        u.FindWindowExW.restype = ctypes.c_void_p
        u.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, w.LPCWSTR, w.LPCWSTR]
        u.RegisterWindowMessageW.argtypes = [w.LPCWSTR]
        u.AllowSetForegroundWindow.argtypes = [w.DWORD]
        k.GetModuleHandleW.restype = ctypes.c_void_p
        k.GetModuleHandleW.argtypes = [w.LPCWSTR]
        self.taskbar_created = u.RegisterWindowMessageW("TaskbarCreated")
        self._hicon = None
        self._proc = None
        self._hmod = k.GetModuleHandleW(None)

        class GUID(ctypes.Structure):
            _fields_ = [("a", w.DWORD), ("b", w.WORD), ("c", w.WORD), ("d", ctypes.c_ubyte * 8)]

        class NID(ctypes.Structure):
            _fields_ = [("cbSize", w.DWORD), ("hWnd", ctypes.c_void_p), ("uID", w.UINT),
                        ("uFlags", w.UINT), ("uCallbackMessage", w.UINT),
                        ("hIcon", ctypes.c_void_p), ("szTip", w.WCHAR * 128),
                        ("dwState", w.DWORD), ("dwStateMask", w.DWORD),
                        ("szInfo", w.WCHAR * 256), ("uVersion", w.UINT),
                        ("szInfoTitle", w.WCHAR * 64), ("dwInfoFlags", w.DWORD),
                        ("guidItem", GUID), ("hBalloonIcon", ctypes.c_void_p)]

        class WNDCLASS(ctypes.Structure):
            _fields_ = [("style", w.UINT), ("lpfnWndProc", self.WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
                        ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                        ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR)]

        self.NID, self.WNDCLASS = NID, WNDCLASS
        s.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.c_void_p]

    def create_window(self, handler) -> int:
        ct = self.ct

        def proc(hwnd, msg, wparam, lparam):
            try:
                if handler(msg, wparam, lparam):
                    return 0
            except Exception:
                pass
            return self.u.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = self.WNDPROC(proc)          # keep the callback alive
        wc = self.WNDCLASS()
        wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = self._proc, self._hmod, CLASS_NAME
        self.u.RegisterClassW(ct.byref(wc))      # 0 when already registered by this process: fine
        HWND_MESSAGE = ct.c_void_p(-3)
        hwnd = self.u.CreateWindowExW(0, CLASS_NAME, CLASS_NAME, 0, 0, 0, 0, 0, HWND_MESSAGE,
                                      None, self._hmod, None)
        return hwnd or 0

    def destroy_window(self, hwnd) -> None:
        self.u.DestroyWindow(hwnd)

    def _icon(self, ico: str):
        if self._hicon is None:
            size = self.u.GetSystemMetrics(49)    # SM_CXSMICON
            handle = self.u.LoadImageW(None, ico, 1, size, size, 0x10) if os.path.isfile(ico) else None
            self._owned = bool(handle)
            self._hicon = handle or self.u.LoadIconW(None, ct_intres(32512))   # IDI_APPLICATION
        return self._hicon

    def notify(self, action: int, hwnd, ico: str, tip: str) -> bool:
        nid = self.NID()
        nid.cbSize = self.ct.sizeof(self.NID)
        nid.hWnd, nid.uID = hwnd, 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._icon(ico)
        nid.szTip = tip[:127]
        ok = bool(self.s.Shell_NotifyIconW(action, self.ct.byref(nid)))
        if action == NIM_DELETE and self._hicon and getattr(self, "_owned", False):
            self.u.DestroyIcon(self._hicon)
            self._hicon = None
        return ok

    def cursor_pos(self) -> tuple[int, int]:
        pt = self.w.POINT()
        self.u.GetCursorPos(self.ct.byref(pt))
        return pt.x, pt.y

    def track_menu(self, hwnd, items, pos) -> str | None:
        menu = self.u.CreatePopupMenu()
        ids: dict[int, str] = {}
        try:
            for n, (action, label) in enumerate(items, 1):
                if action is None:
                    self.u.AppendMenuW(menu, 0x800, 0, None)         # MF_SEPARATOR
                else:
                    self.u.AppendMenuW(menu, 0, n, label)            # MF_STRING
                    ids[n] = action
            self.u.SetMenuDefaultItem(menu, 1, 0)                    # "Aç" in bold
            self.u.SetForegroundWindow(hwnd)     # required, else the menu will not close on outside click
            # TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_BOTTOMALIGN
            cmd = self.u.TrackPopupMenu(menu, 0x0100 | 0x0002 | 0x0020, pos[0], pos[1], 0, hwnd, None)
            self.u.PostMessageW(hwnd, WM_NULL, 0, 0)
            return ids.get(cmd)
        finally:
            self.u.DestroyMenu(menu)

    def post_restore(self) -> bool:
        hwnd = self.u.FindWindowExW(self.ct.c_void_p(-3), None, CLASS_NAME, None)
        if not hwnd:
            return False
        self.u.AllowSetForegroundWindow(0xFFFFFFFF)   # ASFW_ANY: this process just got the user's click
        return bool(self.u.PostMessageW(hwnd, WM_RESTORE, 0, 0))


def ct_intres(n: int):
    """MAKEINTRESOURCE for a ``c_void_p`` argument."""
    import ctypes
    return ctypes.c_void_p(n)
