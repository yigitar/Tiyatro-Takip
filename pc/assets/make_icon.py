"""Generates the app icon (stage with curtains) with the standard library only.

Run:  python pc/assets/make_icon.py        -> pc/assets/tiyatro.ico (16..256) + tiyatro.png (256)
Flat colours, drawn by coverage tests with 4x4 supersampling; PNG chunks (zlib) are embedded in
the ICO container. Deterministic: running it again rewrites identical files.
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIZES = (16, 24, 32, 48, 64, 256)
NAVY, NAVY_LIGHT = (31, 42, 68), (52, 69, 111)
CURTAIN, CURTAIN_FOLD = (155, 28, 46), (122, 20, 36)
GOLD, LIGHT = (224, 165, 38), (244, 217, 139)


def _in_round_square(x: float, y: float, margin: float = 0.03, radius: float = 0.18) -> bool:
    lo, hi = margin, 1 - margin
    if not (lo <= x <= hi and lo <= y <= hi):
        return False
    cx = min(max(x, lo + radius), hi - radius)
    cy = min(max(y, lo + radius), hi - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def _curtain_edge(y: float) -> float:
    """Inner edge of the left curtain (tied back around y = 0.6, flaring above/below)."""
    return 0.17 + 0.16 * ((y - 0.6) / 0.4) ** 2 if y > 0.6 else 0.17 + 0.13 * ((0.6 - y) / 0.5) ** 2


def colour_at(x: float, y: float):
    """RGB of the icon at a point of the unit square, or None (transparent)."""
    if not _in_round_square(x, y):
        return None
    valance_bottom = 0.15 + 0.06 * abs(math.sin(math.pi * 5 * (x - 0.03) / 0.94))
    if y < valance_bottom:
        return GOLD
    mx = x if x <= 0.5 else 1 - x            # the curtains mirror each other
    if mx < _curtain_edge(y):
        fold = math.sin(mx * 2 * math.pi * 9 / 0.5)
        return CURTAIN_FOLD if fold > 0.55 else CURTAIN
    # stage opening: spotlight cone from the top, pool of light on the floor
    cone = 0.07 + 0.30 * (y - 0.2) / 0.7
    if y > 0.2 and abs(x - 0.5) < cone and y < 0.80:
        return NAVY_LIGHT
    if ((x - 0.5) / 0.27) ** 2 + ((y - 0.84) / 0.075) ** 2 <= 1:
        return LIGHT
    return NAVY


def render(size: int, ss: int = 4) -> bytes:
    """RGBA bytes (row-major) of the icon at size x size."""
    out = bytearray()
    for py in range(size):
        for px in range(size):
            r = g = b = a = 0
            for sy in range(ss):
                for sx in range(ss):
                    c = colour_at((px + (sx + 0.5) / ss) / size, (py + (sy + 0.5) / ss) / size)
                    if c is not None:
                        r, g, b, a = r + c[0], g + c[1], b + c[2], a + 1
            if a:
                out += bytes((r // a, g // a, b // a, round(255 * a / (ss * ss))))
            else:
                out += b"\0\0\0\0"
    return bytes(out)


def png(size: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\0" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def ico(images: list[tuple[int, bytes]]) -> bytes:
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return head + entries + blobs


def main() -> None:
    images = [(s, png(s, render(s))) for s in SIZES]
    (HERE / "tiyatro.ico").write_bytes(ico(images))
    (HERE / "tiyatro.png").write_bytes(images[-1][1])
    print("wrote", HERE / "tiyatro.ico", "and tiyatro.png")


if __name__ == "__main__":
    main()
