"""Wave 15 B: console-less launch (pc/launcher.py, pc/tiyatro_takip.pyw, start.bat, Run key)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pc import launcher as LA
from pc import winsetup as W

REPO = Path(__file__).resolve().parent.parent


def test_pyw_file_exists_and_only_delegates():
    src = (REPO / "pc" / "tiyatro_takip.pyw").read_text(encoding="utf-8")
    assert "from pc.launcher import main" in src and "sys.exit(main())" in src
    compile(src, "tiyatro_takip.pyw", "exec")


def test_start_bat_is_windowless_and_exits():
    raw = (REPO / "pc" / "start.bat").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")      # CRLF
    text = raw.decode("ascii").lower()
    assert r'start "" ".venv\scripts\pythonw.exe" "pc\tiyatro_takip.pyw"' in text
    assert r'start "" pyw "pc\tiyatro_takip.pyw"' in text
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    assert lines[-1] == "exit"                       # no lingering cmd window
    assert "python.exe" not in text.replace("pythonw.exe", "")
    assert "pause" not in text


def _fake_repo(tmp_path, with_venv=True):
    (tmp_path / "pc").mkdir()
    if with_venv:
        (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
        (tmp_path / ".venv" / "Scripts" / "pythonw.exe").write_bytes(b"")
    return tmp_path


def test_outside_the_venv_a_windowless_detached_child_is_started(tmp_path, monkeypatch):
    repo = _fake_repo(tmp_path)
    calls = []
    monkeypatch.chdir(tmp_path)
    ran = []
    rc = LA.launch(repo, popen=lambda *a, **k: calls.append((a, k)), run=lambda: ran.append(1),
                   prefix=str(tmp_path / "elsewhere"))
    assert rc == 0 and ran == []
    (args, kw), = calls
    assert args[0] == [str(repo / ".venv" / "Scripts" / "pythonw.exe"), "-m", "pc.app"]
    assert kw["cwd"] == str(repo)
    if sys.platform == "win32":
        assert kw["creationflags"] & LA.CREATE_NO_WINDOW
    assert kw["stdin"] == kw["stdout"] == kw["stderr"] == subprocess.DEVNULL


def test_inside_the_venv_the_app_runs_in_process(tmp_path, monkeypatch):
    repo = _fake_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    ran, calls = [], []
    rc = LA.launch(repo, popen=lambda *a, **k: calls.append(1), run=lambda: ran.append(1) or 7,
                   prefix=str(repo / ".venv"))
    assert rc == 7 and ran == [1] and calls == []


def test_without_a_venv_it_runs_in_process_and_crash_is_reported(tmp_path, monkeypatch):
    repo = _fake_repo(tmp_path, with_venv=False)
    monkeypatch.chdir(tmp_path)
    assert LA.launch(repo, run=lambda: 3, popen=None) == 3


def test_launcher_failure_goes_to_crash_log_not_silence(tmp_path, monkeypatch):
    import pc.app as A
    repo = _fake_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    seen = []
    monkeypatch.setattr(A, "default_paths", lambda: A.CrashPaths(tmp_path / "crash.log",
                                                                 tmp_path / "fallback.log"))
    monkeypatch.setattr(A, "native_box", lambda title, text: seen.append(text))
    monkeypatch.setattr(A, "report_crash", lambda exc, box=None, paths=None: A.write_crash(
        "boom", A.default_paths()) and seen.append("shown"))

    def boom(*a, **k):
        raise OSError("spawn failed")

    assert LA.main(repo, popen=boom, prefix=str(tmp_path / "x")) == 1
    assert (tmp_path / "crash.log").read_text(encoding="utf-8").startswith("boom")
    assert seen == ["shown"]


def test_autostart_run_key_uses_the_same_launcher(tmp_path):
    repo = _fake_repo(tmp_path)
    (repo / "pc" / "tiyatro_takip.pyw").write_text("x", encoding="utf-8")
    py = repo / ".venv" / "Scripts" / "pythonw.exe"
    cmd = W.autostart_command(repo, py)
    assert cmd == f'"{py}" "{repo / "pc" / "tiyatro_takip.pyw"}"'
    assert "python.exe" not in cmd.replace("pythonw.exe", "")
    with pytest.raises(OSError):                 # launcher missing: refuse instead of a dead entry
        W.autostart_command(tmp_path / "nope", py)


def test_real_launcher_file_matches_what_the_run_key_points_to():
    assert (REPO / "pc" / "tiyatro_takip.pyw").is_file()
    cmd = W.autostart_command(REPO, REPO / ".venv" / "Scripts" / "pythonw.exe")
    assert cmd.endswith('tiyatro_takip.pyw"')
