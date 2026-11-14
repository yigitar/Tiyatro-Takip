"""Tiyatro Takip PC app entry point: ``python -m pc.app`` (or ``python -m pc``).

Deliberately tiny and stdlib-only at import time: under ``pythonw`` there is no console,
so any failure (even a missing module or a broken Tk install) must end in a crash file
and a native message box instead of a silent exit. The window itself lives in ``pc.ui``
and is imported lazily inside :func:`run`.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path

APP_TITLE = "Tiyatro Takip"
_REPO = Path(__file__).resolve().parents[1]
APP_LOG = _REPO / "pc" / "local" / "app.log"


@dataclass(frozen=True)
class CrashPaths:
    primary: Path
    fallback: Path


def default_paths() -> CrashPaths:
    return CrashPaths(_REPO / "pc" / "local" / "crash.log",
                      Path(tempfile.gettempdir()) / "tiyatro-takip-crash.log")


_ACTIVE: tuple | None = None  # (box, paths) while run() is active (for the hooks)

_SHAPES = (re.compile(r"github_pat_[A-Za-z0-9_]+"), re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
           re.compile(r"\d{5,}:[A-Za-z0-9_-]{8,}"))
_BEARER = re.compile(r"(Bearer\s+)\S+", re.I)


def _scrub(text: str) -> str:
    for pattern in _SHAPES:
        text = pattern.sub("***", text)
    return _BEARER.sub(r"\1***", text)


def ensure_streams() -> None:
    """pythonw leaves stdout/stderr as None; make print()/logging safe."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def native_box(title: str, text: str) -> None:
    """Message box that needs neither Tk nor a console."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)  # MB_ICONERROR
    except Exception:  # not Windows / no GUI
        try:
            print(f"{title}: {text}", file=sys.stderr)
        except Exception:
            pass


def write_crash(text: str, paths: CrashPaths) -> Path | None:
    """Write to ``primary``, else ``fallback``; returns the path used (None = both failed)."""
    for path in (paths.primary, paths.fallback):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(text.rstrip() + "\n\n")
            return path
        except OSError:
            continue
    return None


def report_crash(exc_info, box=native_box, paths: CrashPaths | None = None) -> None:
    paths = paths or default_paths()
    detail = "".join(traceback.format_exception(*exc_info))
    from datetime import datetime
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    used = write_crash(_scrub(f"[{stamp}] {APP_TITLE}\n{detail}"), paths)
    where = (f"Ayrıntı: {used}" if used else
             f"Ayrıntı dosyasına yazılamadı ({paths.primary}; {paths.fallback}).")
    short = _scrub(f"{type(exc_info[1]).__name__}: {exc_info[1]}")[:300]
    try:
        box(APP_TITLE, f"Uygulama başlatılamadı veya beklenmedik şekilde durdu.\n\n{short}\n\n"
                       f"{where}\nGünlük (varsa): {APP_LOG}")
    except Exception:
        pass


def excepthook(exc_type, exc, tb) -> None:
    box, paths = _ACTIVE or (native_box, None)
    report_crash((exc_type, exc, tb), box, paths)


def thread_excepthook(args) -> None:
    if args.exc_type is SystemExit:
        return
    excepthook(args.exc_type, args.exc_value, args.exc_traceback)


def _load_main():
    from pc.ui import main  # heavy imports (tkinter, tiyatro.*) happen here, guarded
    return main


def run(main=None, *, box=native_box, crash_paths: CrashPaths | None = None) -> int:
    global _ACTIVE
    ensure_streams()
    paths = crash_paths or default_paths()
    _ACTIVE = (box, paths)
    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook
    try:
        return (main or _load_main())()
    except SystemExit:
        raise
    except BaseException:
        report_crash(sys.exc_info(), box, paths)
        return 1


if __name__ == "__main__":
    sys.exit(run())
