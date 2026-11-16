"""Wave 19A: native Windows look. No custom hex colours outside pc/theme.py; the information
colours stay readable on the system window colour; the tooltip uses the system tooltip colours."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from pc import theme as T

PC = Path(__file__).resolve().parents[1] / "pc"


def test_no_custom_hex_colours_outside_theme():
    for f in PC.glob("*.py"):
        if f.name == "theme.py":
            continue
        assert not re.findall(r"[\"']#[0-9a-fA-F]{6}[\"']", f.read_text(encoding="utf8")), f.name


@pytest.mark.parametrize("name", ["GREEN", "AMBER", "RED", "GRAY", "DERIVED", "NEW"])
def test_information_colours_readable_on_window(name):
    h = getattr(T, name)
    rgb = tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
    assert T.contrast(rgb, T.sys_rgb("window")) >= 4.5


@pytest.mark.parametrize("bg", [(0, 0, 0), (32, 32, 32), (255, 255, 255)])
def test_readable_adapts_to_dark_and_light(bg):
    for hue in ("#1a7f37", "#b36b00", "#c62828", "#6b7280", "#4f6d8f", "#6d28d9"):
        h = T.readable(hue, bg)
        assert T.contrast(tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)), bg) >= 4.5


def test_tooltip_uses_system_tooltip_colours():
    tk = pytest.importorskip("tkinter")
    from pc.ui import Tooltip
    root = tk.Tk()
    try:
        b = tk.Label(root, text="x")
        b.pack()
        tip = Tooltip(b, lambda: "hello")
        tip._show()
        lab = tip.tip.winfo_children()[0]
        assert lab.cget("background") == T.TIP_BG and lab.cget("foreground") == T.TIP_FG
        assert str(lab.cget("font")) == "TkTooltipFont"
        tip._hide()
    finally:
        root.destroy()
