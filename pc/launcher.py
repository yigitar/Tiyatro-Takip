"""Console-less launcher of the PC app (wave 15). Stdlib only, never imports Tk.

Entry points that use it, all without a console window:
  * ``pc/tiyatro_takip.pyw`` (double-click: Windows opens ``.pyw`` with ``pyw.exe`` ->
    ``pythonw.exe``, the same mechanism the other bots use),
  * ``pc/start.bat`` (``start "" pythonw ...`` then exits),
  * the Windows-autostart Run key (``<venv>/Scripts/pythonw.exe <repo>/pc/tiyatro_takip.pyw``).

If the interpreter that opened the ``.pyw`` is not the project's ``.venv`` (double-click
uses the globally installed Python, which lacks our packages), the app is started in the
venv's ``pythonw.exe`` as a detached, window-less child (``CREATE_NO_WINDOW``, null
std handles; the pattern of bot1's watchdog) and the launcher exits. Inside the venv the
app runs in-process. Either way ``pc.app.run`` provides crash.log + message box, and the
named mutex in ``pc.ui.main`` keeps a single copy (a second start just shows the open
window). A failure of the launcher itself is reported the same way.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def venv_pythonw(repo: Path = REPO) -> Path | None:
    path = repo / ".venv" / "Scripts" / "pythonw.exe"
    return path if path.is_file() else None


def in_project_venv(repo: Path = REPO, prefix: str | None = None) -> bool:
    try:
        return Path(prefix or sys.prefix).resolve() == (repo / ".venv").resolve()
    except OSError:
        return False


def child_command(repo: Path = REPO) -> list[str] | None:
    """Command of the detached child: venv pythonw running ``pc.app`` (cwd = repo)."""
    python = venv_pythonw(repo)
    return [str(python), "-m", "pc.app"] if python else None


def launch(repo: Path = REPO, *, popen=subprocess.Popen, run=None, prefix: str | None = None) -> int:
    """Start the app. ``popen`` / ``run`` are injectable for tests."""
    os.chdir(repo)
    folder = str(repo)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    command = child_command(repo)
    if command is None or in_project_venv(repo, prefix):
        if run is None:
            from pc.app import run as run   # noqa: PLW0127 - lazy: stdlib-only module
        return run()
    flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    popen(command, cwd=folder, creationflags=flags, close_fds=True,
          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


def main(repo: Path = REPO, **kw) -> int:
    try:
        return launch(repo, **kw)
    except SystemExit:
        raise
    except BaseException:
        folder = str(repo)
        if folder not in sys.path:
            sys.path.insert(0, folder)
        from pc.app import report_crash
        report_crash(sys.exc_info())
        return 1
