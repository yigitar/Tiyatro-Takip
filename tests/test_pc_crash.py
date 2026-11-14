"""The launcher must never exit silently (pythonw has no console)."""
from __future__ import annotations

import sys
import threading

import pytest

from pc import app as launcher


@pytest.fixture
def paths(tmp_path):
    return launcher.CrashPaths(tmp_path / "repo" / "pc" / "local" / "crash.log",
                               tmp_path / "tmp" / "fallback-crash.log")


@pytest.fixture(autouse=True)
def restore_hooks():
    saved = sys.excepthook, threading.excepthook, sys.stdout, sys.stderr
    yield
    sys.excepthook, threading.excepthook, sys.stdout, sys.stderr = saved


def run_with(main, paths, boxes):
    return launcher.run(main, box=lambda title, text: boxes.append((title, text)),
                        crash_paths=paths)


@pytest.mark.parametrize("exc", [ImportError("No module named 'x'"),
                                 RuntimeError("tk.Tk() failed"), OSError("log dir")])
def test_any_failure_writes_crash_log_and_shows_absolute_path(exc, paths):
    boxes = []

    def main():
        raise exc

    assert run_with(main, paths, boxes) == 1
    text = paths.primary.read_text(encoding="utf-8")
    assert type(exc).__name__ in text and "Traceback" in text
    assert len(boxes) == 1
    assert str(paths.primary) in boxes[0][1] and paths.primary.is_absolute()


def test_import_failure_of_the_ui_module_is_caught(paths, monkeypatch):
    boxes = []

    def broken_loader():
        raise ImportError("No module named 'tkinter'")

    monkeypatch.setattr(launcher, "_load_main", broken_loader)
    assert launcher.run(box=lambda t, x: boxes.append(x), crash_paths=paths) == 1
    assert "tkinter" in paths.primary.read_text(encoding="utf-8")
    assert boxes


def test_unwritable_primary_falls_back_to_temp_file(tmp_path, paths):
    blocker = tmp_path / "repo"
    blocker.write_text("i am a file, so pc/local cannot be created below me")
    boxes = []
    assert run_with(lambda: 1 / 0, paths, boxes) == 1
    assert not paths.primary.exists()
    assert "ZeroDivisionError" in paths.fallback.read_text(encoding="utf-8")
    assert str(paths.fallback) in boxes[0][1]


def test_nothing_writable_still_shows_box(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    bad = launcher.CrashPaths(f / "a" / "crash.log", f / "b" / "crash.log")
    boxes = []
    assert run_with(lambda: 1 / 0, bad, boxes) == 1
    assert boxes and "yazılamadı" in boxes[0][1]


def test_tokens_are_scrubbed_from_crash_log(paths):
    def main():
        raise RuntimeError("GET failed ghp_abcdefghijklmnopqrstuvwxyz0123 Bearer abc.def")

    run_with(main, paths, [])
    text = paths.primary.read_text(encoding="utf-8")
    assert "ghp_abcdef" not in text and "abc.def" not in text and "***" in text


def test_none_streams_are_made_safe(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    launcher.ensure_streams()
    print("hello")
    print("err", file=sys.stderr)
    sys.stdout.flush()


def test_run_survives_none_streams_and_installs_hooks(paths, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    boxes = []
    assert run_with(lambda: 1 / 0, paths, boxes) == 1
    assert sys.excepthook is launcher.excepthook
    assert threading.excepthook is launcher.thread_excepthook


def test_excepthooks_write_crash_log(paths, monkeypatch):
    boxes = []
    monkeypatch.setattr(launcher, "_ACTIVE", (lambda t, x: boxes.append(x), paths))
    try:
        raise ValueError("from the main thread")
    except ValueError:
        launcher.excepthook(*sys.exc_info())
    assert "from the main thread" in paths.primary.read_text(encoding="utf-8")

    def worker():
        raise KeyError("from a thread")

    t = threading.Thread(target=worker)
    threading.excepthook = launcher.thread_excepthook
    t.start()
    t.join()
    assert "from a thread" in paths.primary.read_text(encoding="utf-8")
    assert len(boxes) == 2


def test_clean_return_and_system_exit_pass_through(paths):
    boxes = []
    assert run_with(lambda: 0, paths, boxes) == 0
    assert run_with(lambda: 2, paths, boxes) == 2
    with pytest.raises(SystemExit):
        run_with(lambda: sys.exit(3), paths, boxes)
    assert boxes == [] and not paths.primary.exists()
