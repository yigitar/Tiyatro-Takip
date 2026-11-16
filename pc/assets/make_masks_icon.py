"""Local-only: turns a masks PNG into the app icon on a transparent background (stdlib only).

  python pc/assets/make_masks_icon.py [source.png]
      source defaults to pc/assets/local/masks_original.png
      writes pc/assets/local/tiyatro.ico (16,24,32,48,64,256, alpha) + tiyatro.png (256)

The round coloured background is removed by a colour key: the key colour is the opaque colour
met first when walking inwards from the edge middles (the disc), pixels close to it become
transparent, with a soft ramp so the mask edges stay anti-aliased. Transparent pixels of the
source stay transparent. The result is cropped to the content and padded to a square.
pc/assets/local/ is git-ignored (the third-party image has an unknown licence); the committed
tiyatro.ico/png (make_icon.py) is the fallback the app uses when no local icon exists.
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL = HERE / "local"
SIZES = (16, 24, 32, 48, 64, 256)
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def read_png(data: bytes) -> tuple[int, int, bytearray]:
    """Decode an 8-bit, non-interlaced PNG (grey, RGB, palette, grey+alpha, RGBA) to RGBA."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, plte, trns, ihdr = 8, b"", None, None, None
    while pos < len(data):
        length, tag = struct.unpack_from(">I4s", data, pos)
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif tag == b"PLTE":
            plte = body
        elif tag == b"tRNS":
            trns = body
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    if ihdr is None:
        raise ValueError("no IHDR")
    w, h, depth, ctype, _comp, _filt, interlace = ihdr
    if depth != 8 or interlace or ctype not in _CHANNELS:
        raise ValueError(f"unsupported PNG (depth {depth}, type {ctype}, interlace {interlace})")
    bpp = _CHANNELS[ctype]
    stride = w * bpp
    raw = zlib.decompress(idat)
    rows, prev = [], bytearray(stride)
    for y in range(h):
        ft = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if ft == 1:
                line[i] = (line[i] + a) & 255
            elif ft == 2:
                line[i] = (line[i] + b) & 255
            elif ft == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
            elif ft != 0:
                raise ValueError("bad PNG filter")
        rows.append(line)
        prev = line
    out = bytearray(w * h * 4)
    for y, line in enumerate(rows):
        for x in range(w):
            o = (y * w + x) * 4
            if ctype == 6:
                out[o:o + 4] = line[x * 4:x * 4 + 4]
            elif ctype == 2:
                out[o:o + 4] = bytes((*line[x * 3:x * 3 + 3], 255))
            elif ctype == 0:
                out[o:o + 4] = bytes((line[x],) * 3 + (255,))
            elif ctype == 4:
                out[o:o + 4] = bytes((line[2 * x],) * 3 + (line[2 * x + 1],))
            else:
                idx = line[x]
                alpha = trns[idx] if trns and idx < len(trns) else 255
                out[o:o + 4] = bytes((*plte[idx * 3:idx * 3 + 3], alpha))
    return w, h, out


def write_png(w: int, h: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    rows = b"".join(b"\0" + bytes(rgba[y * w * 4:(y + 1) * w * 4]) for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def _dist(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def find_key_colour(w: int, h: int, px: bytearray) -> tuple[int, int, int] | None:
    """The disc colour: first opaque pixel walking inwards from each edge middle (most common)."""
    votes: dict[tuple[int, int, int], int] = {}
    walks = (((w // 2, 0), (0, 1)), ((w // 2, h - 1), (0, -1)),
             ((0, h // 2), (1, 0)), ((w - 1, h // 2), (-1, 0)))
    for (x, y), (dx, dy) in walks:
        while 0 <= x < w and 0 <= y < h:
            o = (y * w + x) * 4
            if px[o + 3] > 200:
                col = tuple(v // 8 * 8 for v in px[o:o + 3])
                votes[col] = votes.get(col, 0) + 1
                break
            x, y = x + dx, y + dy
    if not votes:
        return None
    best = max(votes, key=votes.get)
    # refine: the exact colour of one pixel of the winning bucket
    for (x, y), (dx, dy) in walks:
        while 0 <= x < w and 0 <= y < h:
            o = (y * w + x) * 4
            if px[o + 3] > 200:
                if tuple(v // 8 * 8 for v in px[o:o + 3]) == best:
                    return tuple(px[o:o + 3])
                break
            x, y = x + dx, y + dy
    return best


def remove_background(w: int, h: int, px: bytearray, *, tol: float = 40.0, soft: float = 140.0,
                      key: tuple[int, int, int] | None = None) -> bytearray:
    """Colour-key the disc away: distance <= tol -> transparent; tol..tol+soft -> partial alpha
    (colour un-mixed from the key so no coloured fringe is left); further -> unchanged."""
    key = key or find_key_colour(w, h, px)
    out = bytearray(px)
    if key is None:
        return out
    for i in range(0, w * h * 4, 4):
        a = px[i + 3]
        if a == 0:
            continue
        d = _dist(px[i:i + 3], key)
        if d <= tol:
            out[i:i + 4] = b"\0\0\0\0"
        elif d < tol + soft:
            t = (d - tol) / soft                      # 0..1 share of the foreground
            out[i + 3] = round(a * t)
            for c in range(3):                         # undo the mix with the key colour
                out[i + c] = max(0, min(255, round(key[c] + (px[i + c] - key[c]) / t)))
    return out


def crop_square(w: int, h: int, px: bytearray, margin: float = 0.04) -> tuple[int, bytearray]:
    xs = [i // 4 % w for i in range(3, w * h * 4, 4) if px[i] > 8]
    ys = [i // 4 // w for i in range(3, w * h * 4, 4) if px[i] > 8]
    if not xs:
        raise ValueError("nothing left after background removal")
    x0, x1, y0, y1 = min(xs), max(xs) + 1, min(ys), max(ys) + 1
    side = round(max(x1 - x0, y1 - y0) * (1 + 2 * margin))
    ox, oy = (x0 + x1 - side) // 2, (y0 + y1 - side) // 2
    out = bytearray(side * side * 4)
    for y in range(side):
        sy = y + oy
        if not 0 <= sy < h:
            continue
        for x in range(side):
            sx = x + ox
            if 0 <= sx < w:
                out[(y * side + x) * 4:(y * side + x) * 4 + 4] = px[(sy * w + sx) * 4:(sy * w + sx) * 4 + 4]
    return side, out


def resize(side: int, px: bytearray, size: int) -> bytes:
    """Area-average resampling on premultiplied alpha (no dark fringes at transparent edges)."""
    if side == size:
        return bytes(px)
    scale = side / size

    def axis(n_out):
        spans = []
        for o in range(n_out):
            a, b = o * scale, (o + 1) * scale
            i0, i1 = int(a), min(side, int(b) + (1 if b % 1 else 0))
            spans.append([(i, min(b, i + 1) - max(a, i)) for i in range(i0, i1)])
        return spans

    sp = axis(size)
    pre = [0.0] * (side * side * 4)
    for i in range(0, side * side * 4, 4):
        a = px[i + 3] / 255
        pre[i], pre[i + 1], pre[i + 2], pre[i + 3] = px[i] * a, px[i + 1] * a, px[i + 2] * a, a
    tmp = [0.0] * (size * side * 4)               # horizontal pass: side rows x size cols
    for y in range(side):
        for ox, span in enumerate(sp):
            for c in range(4):
                tmp[(y * size + ox) * 4 + c] = sum(pre[(y * side + i) * 4 + c] * wgt for i, wgt in span) / scale
    out = bytearray(size * size * 4)
    for oy, span in enumerate(sp):
        for ox in range(size):
            v = [sum(tmp[(i * size + ox) * 4 + c] * wgt for i, wgt in span) / scale for c in range(4)]
            o = (oy * size + ox) * 4
            if v[3] > 1e-6:
                out[o:o + 4] = bytes((min(255, round(v[0] / v[3])), min(255, round(v[1] / v[3])),
                                      min(255, round(v[2] / v[3])), min(255, round(v[3] * 255))))
    return bytes(out)


def ico(images: list[tuple[int, bytes]]) -> bytes:
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return head + entries + blobs


def build(source: Path, out_dir: Path, sizes=SIZES, **kw) -> list[Path]:
    w, h, px = read_png(source.read_bytes())
    px = remove_background(w, h, px, **kw)
    side, px = crop_square(w, h, px)
    images = [(s, write_png(s, s, resize(side, px, s))) for s in sizes]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "tiyatro.ico").write_bytes(ico(images))
    (out_dir / "tiyatro.png").write_bytes(images[-1][1])
    return [out_dir / "tiyatro.ico", out_dir / "tiyatro.png"]


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else LOCAL / "masks_original.png"
    if not source.is_file():
        print("source image not found:", source)
        return 1
    for path in build(source, LOCAL):
        print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
