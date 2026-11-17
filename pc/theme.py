"""Wave 19A: the native Windows look. Colours are the system colours (Tk's ``System*`` names, which
Tk resolves with GetSysColor); the few that carry information (status icon hues, derived / new
item text) are derived from the system window colours so they stay readable on any system
theme. No Tk root needed: the system colours are read with ``GetSysColor`` (fallback: the
classic light values)."""
from __future__ import annotations

import colorsys
import sys

# Tk colour names (valid in every widget option and canvas item on Windows)
WINDOW, WINDOW_TEXT = "SystemWindow", "SystemWindowText"
FACE, FACE_TEXT, SHADOW = "SystemButtonFace", "SystemButtonText", "SystemButtonShadow"
HILITE, HILITE_TEXT = "SystemHighlight", "SystemHighlightText"
GRAY_TEXT = "SystemGrayText"
TIP_BG, TIP_FG = "SystemInfoBackground", "SystemInfoText"

# COLOR_* indexes for GetSysColor
_IDX = {"window": 5, "text": 8, "face": 15, "hilite": 13, "gray": 17, "hotlight": 26}
_FALLBACK = {"window": (255, 255, 255), "text": (0, 0, 0), "face": (240, 240, 240),
             "hilite": (0, 120, 215), "gray": (109, 109, 109), "hotlight": (0, 102, 204)}


def sys_rgb(name: str) -> tuple[int, int, int]:
    if sys.platform == "win32":
        try:
            import ctypes
            v = ctypes.windll.user32.GetSysColor(_IDX[name])
            return v & 255, (v >> 8) & 255, (v >> 16) & 255
        except Exception:   # pragma: no cover
            pass
    return _FALLBACK[name]


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(int(round(max(0, min(255, c)))) for c in rgb)


def _lum(rgb) -> float:
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mix(a, b, t: float) -> str:
    """Colour ``t`` of the way from a to b (a, b: rgb tuples)."""
    return _hex([x + (y - x) * t for x, y in zip(a, b)])


def readable(hue_hex: str, bg=None, minimum: float = 4.5) -> str:
    """The given hue, its lightness moved (hue and saturation kept) until it reaches the contrast
    ``minimum`` against ``bg`` (default: the system window colour); unchanged when it already does."""
    bg = bg or sys_rgb("window")
    rgb = tuple(int(hue_hex[i:i + 2], 16) for i in (1, 3, 5))
    if contrast(rgb, bg) >= minimum:
        return hue_hex
    h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    step = 0.02 if _lum(bg) < 0.4 else -0.02     # dark system theme: lighten, light: darken
    for _ in range(50):
        l = max(0.0, min(1.0, l + step))
        cand = tuple(c * 255 for c in colorsys.hls_to_rgb(h, l, s))
        if contrast(cand, bg) >= minimum:
            return _hex(cand)
    return _hex(cand)


def dark() -> bool:
    return _lum(sys_rgb("window")) < 0.4


# meaningful colours (status icon hues, provenance), derived to be readable on the window colour
GREEN = readable("#1a7f37")
AMBER = readable("#b36b00")
RED = readable("#c62828")
GRAY = readable("#6b7280")
DERIVED = readable("#4f6d8f")      # italic derived / assumed text
BLUE = readable("#1d4ed8")         # notification rows of the activity log
NEW = readable("#6d28d9")          # bold name of an unopened item
# faint tints made of the system colours (zebra stripe / row tints / grid lines)
ZEBRA = mix(sys_rgb("window"), sys_rgb("text"), 0.035)
GRID = mix(sys_rgb("window"), sys_rgb("text"), 0.12)
LINK = _hex(sys_rgb("hotlight"))   # the system hyperlink colour
WARN_ROW = mix(sys_rgb("window"), (255, 193, 7), 0.22)    # tints keep their meaning
ERROR_ROW = mix(sys_rgb("window"), (220, 38, 38), 0.18)


def rgb_of(hex_colour: str) -> tuple[int, int, int]:
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))   # type: ignore[return-value]
