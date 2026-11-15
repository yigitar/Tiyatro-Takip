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
    """Command line stored in the Run key: pythonw (no console window) running ``pc.app``
    with the project folder as the working directory. The Run key cannot set a working
    directory, so the one-liner changes into it first and then runs the module exactly as
    ``python -m pc.app`` would (``pc.app`` is not importable from anywhere else)."""
    python = Path(python) if python else pythonw_path(repo)
    if python is None:
        raise OSError("pythonw.exe bulunamadı")
    folder = str(repo)
    if "'" in folder or '"' in folder:
        raise OSError("proje klasörü yolunda tırnak işareti var")
    code = (f"import os,sys,runpy;os.chdir(r'{folder}');sys.path.insert(0,r'{folder}');"
            f"runpy.run_module('pc.app',run_name='__main__')")
    return f'"{python}" -c "{code}"'


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
