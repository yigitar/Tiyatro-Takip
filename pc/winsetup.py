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


def apply_window_icon(root, ico: Path | None = None, png: Path | None = None) -> str:
    """Window / taskbar icon of a Tk root: the multi-size .ico on Windows (iconbitmap), else /
    as a fallback the PNG (wm_iconphoto). Missing file or Tk error: no icon, no exception.
    Returns "ico", "png" or ""."""
    if ico is None or png is None:
        d_ico, d_png = icon_files()
        ico, png = ico or d_ico, png or d_png
    try:
        if os.name == "nt" and ico.is_file():
            root.iconbitmap(str(ico))              # this window (WM_SETICON big + small)
            root.iconbitmap(default=str(ico))      # every later Toplevel (Popup, editors, dialogs)
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


def is_windows() -> bool:
    return os.name == "nt"


def info_box(title: str, text: str) -> None:
    """Native message box (needs no Tk); silently skipped when not on Windows."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)   # MB_ICONINFORMATION
    except Exception:
        pass
