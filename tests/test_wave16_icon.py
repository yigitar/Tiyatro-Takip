"""Wave 16: icon file, taskbar identity, window icon, shortcut script."""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from pc import winsetup

ROOT = Path(__file__).resolve().parents[1]


def test_ico_has_all_sizes_with_png_payloads():
    data = (ROOT / "pc" / "assets" / "tiyatro.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind) == (0, 1) and count == 6
    sizes = []
    for i in range(count):
        w, h, _c, _r, _p, bits, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        sizes.append(w or 256)
        assert (w or 256) == (h or 256) and bits == 32
        assert data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n" and offset + length <= len(data)
    assert sizes == [16, 24, 32, 48, 64, 256]


def test_generator_is_deterministic(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("make_icon", ROOT / "pc" / "assets" / "make_icon.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "HERE", tmp_path)
    monkeypatch.setattr(mod, "SIZES", (16, 32))
    mod.main()
    assert (tmp_path / "tiyatro.png").read_bytes().startswith(b"\x89PNG")


def test_app_user_model_id_is_guarded():
    class Api:
        def __init__(self, result): self.result, self.seen = result, None
        def SetCurrentProcessExplicitAppUserModelID(self, value):
            self.seen = value
            return self.result
    ok = Api(0)
    assert winsetup.set_app_user_model_id(api=ok) is True and ok.seen == winsetup.APP_USER_MODEL_ID
    assert winsetup.set_app_user_model_id(api=Api(1)) is False

    class Boom:
        def SetCurrentProcessExplicitAppUserModelID(self, value): raise OSError("no")
    assert winsetup.set_app_user_model_id(api=Boom()) is False


def test_apply_window_icon_never_raises(tmp_path):
    class Root:
        def iconbitmap(self, **kw): raise RuntimeError("tk")
        def wm_iconphoto(self, *a): raise RuntimeError("tk")
    assert winsetup.apply_window_icon(Root(), tmp_path / "none.ico", tmp_path / "none.png") == ""


def test_apply_window_icon_on_real_root():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk unavailable")
    root.withdraw()
    try:
        assert winsetup.apply_window_icon(root) in ("ico", "png")
    finally:
        root.destroy()


def test_shortcut_script_has_no_absolute_paths_or_personal_info():
    text = (ROOT / "scripts" / "make_shortcut.ps1").read_text(encoding="utf-8")
    for bad in ("E:\\", "C:\\", "D:\\", "AYIGIT", "Users"):
        assert bad not in text
    assert "$PSScriptRoot" in text and "pythonw.exe" in text and "tiyatro_takip.pyw" in text
