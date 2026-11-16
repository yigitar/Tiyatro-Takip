"""Windows integration of the PC app (wave 8): per-user autostart, keep-awake, DPI
awareness and bringing an already running window to the front.

Everything is stdlib (``winreg``, ``ctypes``) and degrades to a no-op/``False`` on other
systems, so the module imports and the tests run anywhere. The registry and the Win32
functions are injectable (``reg=``, ``api=``) so tests never touch the real Run key or the
real power state.

Nothing here runs at import time and nothing is written unless the user toggles an option.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# ---- autostart (HKCU Run key, current user, no admin) ---------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "TiyatroTakip"


def _winreg():
    try:
        import winreg
    except ImportError:  # not Windows
        return None
    return winreg


def pythonw_path(repo: Path = REPO) -> Path | None:
    """The windowless interpreter to start with: the project's venv, else the sibling of
    the running interpreter, else None."""
    venv = repo / ".venv" / "Scripts" / "pythonw.exe"
    if venv.is_file():
        return venv
    sibling = Path(sys.executable).with_name("pythonw.exe")
    return sibling if sibling.is_file() else None


def autostart_command(repo: Path = REPO, python: Path | str | None = None) -> str:
    """Command line stored in the Run key: the project's windowless ``pythonw.exe`` running
    ``pc/tiyatro_takip.pyw`` (the same launcher as the double-click / start.bat; it sets
    the working directory itself, since a Run key cannot)."""
    python = Path(python) if python else pythonw_path(repo)
    if python is None:
        raise OSError("pythonw.exe bulunamadı")
    if '"' in str(repo):
        raise OSError("proje klasörü yolunda tırnak işareti var")
    script = repo / "pc" / "tiyatro_takip.pyw"
    if not script.is_file():
        raise OSError(f"başlatıcı bulunamadı: {script}")
    return f'"{python}" "{script}"'


def autostart_value(reg=None, key: str = RUN_KEY, name: str = RUN_VALUE) -> str | None:
    """Current Run value, or None when it does not exist (or the registry is unavailable)."""
    reg = reg or _winreg()
    if reg is None:
        return None
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, key, 0, reg.KEY_READ) as handle:
            value, _kind = reg.QueryValueEx(handle, name)
    except OSError:
        return None
    return value if isinstance(value, str) else None


def autostart_enabled(reg=None, key: str = RUN_KEY, name: str = RUN_VALUE) -> bool:
    """The real registry state (what the Settings check mark shows)."""
    return autostart_value(reg, key, name) is not None


def set_autostart(enabled: bool, *, reg=None, repo: Path = REPO, python=None,
                  key: str = RUN_KEY, name: str = RUN_VALUE) -> bool:
    """Create (``enabled``) or remove the user-level Run value. Raises ``OSError`` when the
    registry cannot be written. Returns the state read back afterwards."""
    reg = reg or _winreg()
    if reg is None:
        raise OSError("Windows kayıt defteri kullanılamıyor")
    if enabled:
        command = autostart_command(repo, python)
        with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, key, 0, reg.KEY_SET_VALUE) as handle:
            reg.SetValueEx(handle, name, 0, reg.REG_SZ, command)
    else:
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, key, 0, reg.KEY_SET_VALUE) as handle:
                reg.DeleteValue(handle, name)
        except FileNotFoundError:
            pass  # already absent
    return autostart_enabled(reg, key, name)


# ---- keep awake -------------------------------------------------------------------------------
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _kernel32():
    try:
        import ctypes
        return ctypes.windll.kernel32
    except (ImportError, AttributeError, OSError):
        return None


def set_keep_awake(enabled: bool, api=None) -> bool:
    """``SetThreadExecutionState``: while enabled the system does not go to sleep (the
    display may still turn off). The state belongs to the calling thread, so call this
    from the same (UI) thread every time. Returns True when Windows accepted the call."""
    api = api or _kernel32()
    if api is None:
        return False
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if enabled else 0)
    try:
        return bool(api.SetThreadExecutionState(flags))
    except Exception:
        return False


# ---- DPI --------------------------------------------------------------------------------------
def enable_dpi_awareness(api=None) -> str:
    """Call BEFORE the first Tk window is created. System-DPI aware: Windows no longer
    bitmap-stretches the window (blurry), Tk picks the screen's real DPI for its scaling.
    Returns which call worked: "shcore", "user32", "" (none / not Windows)."""
    if api is None:
        try:
            import ctypes
            api = ctypes.windll
        except (ImportError, AttributeError, OSError):
            return ""
    try:
        if api.shcore.SetProcessDpiAwareness(1) in (0, None):   # S_OK
            return "shcore"
    except Exception:
        pass
    try:
        api.user32.SetProcessDPIAware()
        return "user32"
    except Exception:
        return ""


# ---- icon / taskbar identity ------------------------------------------------------------------
APP_USER_MODEL_ID = "TiyatroTakip.PC"
ASSETS = Path(__file__).resolve().parent / "assets"
ICON_ICO = ASSETS / "tiyatro.ico"          # committed (generated) icon = the fallback
ICON_PNG = ASSETS / "tiyatro.png"
ICO_SIGNATURE = bytes((0, 0, 1, 0))


def icon_files(assets: Path = ASSETS) -> tuple[Path, Path]:
    """(ico, png) to use: the git-ignored local icon in ``assets/local`` when its .ico exists
    and starts with the ICO signature, otherwise the committed generated icon."""
    local_ico, local_png = assets / "local" / "tiyatro.ico", assets / "local" / "tiyatro.png"
    try:
        if local_ico.is_file() and local_ico.read_bytes()[:4] == ICO_SIGNATURE:
            return local_ico, (local_png if local_png.is_file() else assets / "tiyatro.png")
    except OSError:
        pass
    return assets / "tiyatro.ico", assets / "tiyatro.png"


def set_app_user_model_id(app_id: str = APP_USER_MODEL_ID, api=None) -> bool:
    """Own taskbar identity, so the taskbar shows our icon instead of pythonw's. Call before
    the first window. Never raises; False when unavailable (not Windows, old Windows)."""
    try:
        if api is None:
            import ctypes
            api = ctypes.windll.shell32
        return api.SetCurrentProcessExplicitAppUserModelID(app_id) in (0, None)   # S_OK
    except Exception:
        return False


def set_window_icons(root, ico: Path, user32=None) -> bool:
    """WM_SETICON (big + small, exact system sizes) for a Tk window, from the .ico file, so
    ``WM_GETICON`` / the taskbar button get a per-window icon and not only the class icon.
    Never raises; False when unavailable."""
    try:
        if user32 is None:
            import ctypes
            user32 = ctypes.windll.user32
            user32.LoadImageW.restype = ctypes.c_void_p
            user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                            ctypes.c_ssize_t]
            user32.GetParent.restype = ctypes.c_void_p
        root.update_idletasks()                    # the Win32 wrapper window exists after this
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        done = False
        for kind, metric in ((1, 11), (0, 49)):    # ICON_BIG/SM_CXICON, ICON_SMALL/SM_CXSMICON
            size = user32.GetSystemMetrics(metric)
            handle = user32.LoadImageW(None, str(ico), 1, size, size, 0x10)   # IMAGE_ICON, LR_LOADFROMFILE
            if handle:
                user32.SendMessageW(hwnd, 0x80, kind, handle)                 # WM_SETICON
                done = True
        return done
    except Exception:
        return False


def apply_window_icon(root, ico: Path | None = None, png: Path | None = None) -> str:
    """Window / taskbar icon of a Tk root: the multi-size .ico on Windows (iconbitmap), else /
    as a fallback the PNG (wm_iconphoto). Missing file or Tk error: no icon, no exception.
    Returns "ico", "png" or ""."""
    if ico is None or png is None:
        d_ico, d_png = icon_files()
        ico, png = ico or d_ico, png or d_png
    try:
        if os.name == "nt" and ico.is_file():
            # NOT ``root.iconbitmap(path)``: on a withdrawn root it resets the window to 200x200
            root.iconbitmap(default=str(ico))      # class icon: every Toplevel (Popup, editors, dialogs)
            set_window_icons(root, ico)            # this window: WM_SETICON big + small
            return "ico"
    except Exception:
        pass
    try:
        if png.is_file():
            import tkinter as tk
            root._tiyatro_icon = tk.PhotoImage(master=root, file=str(png))   # keep a reference
            root.wm_iconphoto(True, root._tiyatro_icon)
            return "png"
    except Exception:
        pass
    return ""


# ---- single instance helper -------------------------------------------------------------------
def focus_existing_window(title: str, api=None) -> bool:
    """Bring the already running app window (top level Tk window titled ``title``) to the
    front: restore it when minimised, then SetForegroundWindow (Windows may only flash the
    taskbar button if another process owns the foreground; that is its rule). Returns True
    when a window was found."""
    if api is None:
        try:
            import ctypes
            user32 = ctypes.windll.user32
        except (ImportError, AttributeError, OSError):
            return False
    else:
        user32 = api
    try:
        hwnd = user32.FindWindowW("TkTopLevel", title)
        if not hwnd:
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        else:
            user32.ShowWindow(hwnd, 5)   # SW_SHOW (also un-hides a withdrawn window)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _hwnd(widget, user32):
    return user32.GetParent(widget.winfo_id()) or widget.winfo_id()


def work_area(root=None, user32=None) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the WORK AREA (monitor minus taskbar) of the monitor that
    holds ``root`` (MonitorFromWindow + GetMonitorInfo ``rcWork``); falls back to the primary
    work area (SPI_GETWORKAREA), then to the whole screen when Windows cannot say."""
    if root is not None:
        full = (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
    else:
        full = (0, 0, 1280, 720)
    if user32 is None and sys.platform != "win32":
        return full
    try:
        import ctypes
        from ctypes import wintypes
        user32 = user32 or ctypes.windll.user32
        if root is not None:
            try:
                class MONITORINFO(ctypes.Structure):
                    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
                user32.MonitorFromWindow.restype = wintypes.HANDLE
                user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
                user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
                mon = user32.MonitorFromWindow(_hwnd(root, user32), 2)   # MONITOR_DEFAULTTONEAREST
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(MONITORINFO)
                if mon and user32.GetMonitorInfoW(mon, ctypes.byref(info)):
                    r = info.rcWork
                    return r.left, r.top, r.right, r.bottom
            except Exception:
                pass
        rect = wintypes.RECT()
        if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):   # SPI_GETWORKAREA
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return full


def outer_size(window, user32=None) -> tuple[int, int]:
    """Real outer size (frame + title bar included) of a mapped window: GetWindowRect on
    Windows, else the requested client size plus the Tk frame offsets."""
    window.update_idletasks()
    w, h = window.winfo_width(), window.winfo_height()
    if w <= 1 or h <= 1:
        w, h = window.winfo_reqwidth(), window.winfo_reqheight()
    if sys.platform == "win32" or user32 is not None:
        try:
            import ctypes
            from ctypes import wintypes
            user32 = user32 or ctypes.windll.user32
            rect = wintypes.RECT()
            if window.winfo_ismapped() and user32.GetWindowRect(_hwnd(window, user32),
                                                                ctypes.byref(rect)):
                return rect.right - rect.left, rect.bottom - rect.top
        except Exception:
            pass
    border = max(0, window.winfo_rootx() - window.winfo_x())
    title = max(0, window.winfo_rooty() - window.winfo_y() - border)
    return w + 2 * border, h + title + 2 * border


def bottom_right(work, outer, gap) -> tuple[int, int]:
    """Top-left of an ``outer``-sized window put into the bottom-right corner of ``work``
    with ``gap`` margin (never left of / above the work-area origin)."""
    left, top, right, bottom = work
    ow, oh = outer
    return max(left, right - ow - gap), max(top, bottom - oh - gap)


NO_ACTIVATE = 0x08000000      # WS_EX_NOACTIVATE
_GWL_EXSTYLE = -20


def set_no_activate(window, user32=None) -> bool:
    """Show ``window`` without ever taking the keyboard focus (WS_EX_NOACTIVATE): clicking it
    still works, typing elsewhere is not interrupted. False when not possible (not Windows)."""
    if user32 is None and sys.platform != "win32":
        return False
    try:
        import ctypes
        user32 = user32 or ctypes.windll.user32
        window.update_idletasks()
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style | NO_ACTIVATE)
        return bool(user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & NO_ACTIVATE)
    except Exception:
        return False


def is_windows() -> bool:
    return os.name == "nt"


def info_box(title: str, text: str) -> None:
    """Native message box (needs no Tk); silently skipped when not on Windows."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)   # MB_ICONINFORMATION
    except Exception:
        pass
