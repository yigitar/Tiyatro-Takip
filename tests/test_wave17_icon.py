"""Wave 17: local-only masks icon (background removal, ICO build) and the committed fallback."""
from __future__ import annotations

import importlib.util
import struct
import zlib
from pathlib import Path

import pytest

from pc import winsetup

ROOT = Path(__file__).resolve().parents[1]


def _gen():
    spec = importlib.util.spec_from_file_location("make_masks_icon", ROOT / "pc" / "assets" / "make_masks_icon.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _synthetic(n=128):
    """Orange disc on transparent corners; two dark 'masks' (blobs) with a hole each."""
    px = bytearray(n * n * 4)
    c = n / 2
    for y in range(n):
        for x in range(n):
            o = (y * n + x) * 4
            if (x - c) ** 2 + (y - c) ** 2 <= (c - 2) ** 2:
                px[o:o + 4] = bytes((240, 150, 30, 255))
            mask = ((x - 45) ** 2 + (y - 60) ** 2 <= 22 ** 2) or ((x - 85) ** 2 + (y - 70) ** 2 <= 22 ** 2)
            hole = ((x - 45) ** 2 + (y - 55) ** 2 <= 5 ** 2)
            if mask and not hole:
                px[o:o + 4] = bytes((20, 20, 40, 255))
    return n, px


def test_png_roundtrip_and_filters():
    g = _gen()
    n, px = _synthetic(16)
    w, h, back = g.read_png(g.write_png(n, n, bytes(px)))
    assert (w, h) == (16, 16) and bytes(back) == bytes(px)


def test_reads_filtered_rgb_png():
    g = _gen()
    w = h = 4
    rows = bytearray()
    prev = bytes(w * 3)
    raw_rows = [bytes((10 * y + x) % 256 for x in range(w * 3)) for y in range(h)]
    for ft, row in zip((1, 2, 3, 4), raw_rows):
        cur = bytearray()
        for i, v in enumerate(row):
            a = row[i - 3] if i >= 3 else 0
            b = prev[i]
            c = prev[i - 3] if i >= 3 else 0
            if ft == 1: pred = a
            elif ft == 2: pred = b
            elif ft == 3: pred = (a + b) >> 1
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
            cur.append((v - pred) & 255)
        rows += bytes((ft,)) + bytes(cur)
        prev = row

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b""))
    _w, _h, rgba = g.read_png(data)
    for y in range(h):
        for x in range(w):
            assert bytes(rgba[(y * w + x) * 4:(y * w + x) * 4 + 3]) == raw_rows[y][x * 3:x * 3 + 3]
            assert rgba[(y * w + x) * 4 + 3] == 255


def test_background_is_removed_masks_stay():
    g = _gen()
    n, px = _synthetic()
    key = g.find_key_colour(n, n, px)
    assert key == (240, 150, 30)
    out = g.remove_background(n, n, px)
    at = lambda x, y: bytes(out[(y * n + x) * 4:(y * n + x) * 4 + 4])
    assert at(2, 2)[3] == 0                      # corner (was already transparent)
    assert at(10, 64)[3] == 0                    # disc area -> transparent
    assert at(45, 75) == bytes((20, 20, 40, 255))   # mask body untouched
    assert at(45, 55)[3] == 0                    # hole inside a mask is transparent too


def test_antialiased_edge_has_no_orange_fringe():
    g = _gen()
    key = (240, 150, 30)
    # a pixel that is a 50/50 mix of key and dark foreground
    mix = tuple((a + b) // 2 for a, b in zip(key, (20, 20, 40)))
    px = bytearray(bytes(mix) + b"\xff")
    out = g.remove_background(1, 1, px, key=key)
    assert 0 < out[3] < 255
    assert out[0] < 100 and out[1] < 80 and out[0] < mix[0]   # un-mixed: darker than the mix, no orange


def test_build_makes_multi_size_ico_with_alpha(tmp_path):
    g = _gen()
    n, px = _synthetic()
    src = tmp_path / "src.png"
    src.write_bytes(g.write_png(n, n, bytes(px)))
    ico_path, png_path = g.build(src, tmp_path / "out")
    data = ico_path.read_bytes()
    assert data[:4] == winsetup.ICO_SIGNATURE
    count = struct.unpack_from("<H", data, 4)[0]
    sizes = []
    for i in range(count):
        w, _h, _c, _r, _p, bits, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        sizes.append(w or 256)
        blob = data[offset:offset + length]
        assert bits == 32 and blob.startswith(b"\x89PNG")
        sw, sh, rgba = g.read_png(blob)
        assert sw == sh == (w or 256)
        assert rgba[3] == 0 and rgba[-1] == 0          # all four corners transparent
        assert any(rgba[i] > 200 for i in range(3, len(rgba), 4))   # something opaque remains
    assert sizes == [16, 24, 32, 48, 64, 256]
    assert png_path.read_bytes().startswith(b"\x89PNG")


def test_missing_source_is_reported(tmp_path, capsys):
    assert _gen().main(["x", str(tmp_path / "none.png")]) == 1


def test_icon_files_prefers_valid_local_icon(tmp_path):
    (tmp_path / "local").mkdir()
    assert winsetup.icon_files(tmp_path) == (tmp_path / "tiyatro.ico", tmp_path / "tiyatro.png")   # no local
    (tmp_path / "local" / "tiyatro.ico").write_bytes(b"garbage")
    assert winsetup.icon_files(tmp_path)[0] == tmp_path / "tiyatro.ico"                  # corrupt -> fallback
    (tmp_path / "local" / "tiyatro.ico").write_bytes(winsetup.ICO_SIGNATURE + b"rest")
    assert winsetup.icon_files(tmp_path)[0] == tmp_path / "local" / "tiyatro.ico"
    (tmp_path / "local" / "tiyatro.png").write_bytes(b"\x89PNG")
    assert winsetup.icon_files(tmp_path)[1] == tmp_path / "local" / "tiyatro.png"


def test_apply_window_icon_uses_fallback_when_local_missing(monkeypatch, tmp_path):
    seen = {}

    class Root:
        def iconbitmap(self, **kw): seen.update(kw)
        def wm_iconphoto(self, *a): pass
    monkeypatch.setattr(winsetup, "icon_files", lambda assets=None: (winsetup.ICON_ICO, winsetup.ICON_PNG))
    if winsetup.os.name == "nt":
        assert winsetup.apply_window_icon(Root()) == "ico"
        assert Path(seen["default"]) == winsetup.ICON_ICO
    assert winsetup.ICON_ICO.is_file() and winsetup.ICON_PNG.is_file()      # committed fallback exists


def test_local_icon_is_git_ignored_and_not_tracked():
    import subprocess
    ignored = subprocess.run(["git", "check-ignore", "pc/assets/local/tiyatro.ico", "pc/assets/local/masks_original.png"],
                             cwd=ROOT, capture_output=True, text=True)
    assert ignored.returncode == 0 and ignored.stdout.count("pc/assets/local/") == 2
    tracked = subprocess.run(["git", "ls-files", "pc/assets"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert not any("/local/" in t for t in tracked)
    assert any(t.endswith("tiyatro.ico") for t in tracked)
