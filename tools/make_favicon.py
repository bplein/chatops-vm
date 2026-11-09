"""Render favicon PNGs from the same geometry as app/static/favicon.svg.

Pure stdlib (zlib/struct) so it runs anywhere, no Pillow: supersample 4x and
box-filter down. Run from repo root:  python3 tools/make_favicon.py
Regenerate whenever the SVG changes.
"""

import struct
import zlib
from pathlib import Path

VIEW = 64.0
SS = 4  # supersample factor
DARK_BG = (16, 18, 22, 255)
DARK_INK = (237, 239, 244, 255)
LIGHT_BG = (255, 255, 255, 255)
LIGHT_INK = (27, 31, 42, 255)

RECT = (0.0, 0.0, 64.0, 64.0, 14.0)  # x, y, w, h, r
POLY = [(20, 19), (36, 32), (20, 45)]
STROKE_W = 7.0
BLOCK = (40.0, 38.0, 10.0, 10.0, 2.0)


def in_rounded_rect(px, py, x, y, w, h, r):
    if not (x <= px <= x + w and y <= py <= y + h):
        return False
    cx = min(max(px, x + r), x + w - r)
    cy = min(max(py, y + r), y + h - r)
    return (px - cx) ** 2 + (py - cy) ** 2 <= r * r


def dist_to_poly(px, py):
    best = float("inf")
    for (ax, ay), (bx, by) in zip(POLY, POLY[1:]):
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        best = min(best, ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5)
    return best


def sample(px, py, dark):
    bg, ink = (DARK_BG, DARK_INK) if dark else (LIGHT_BG, LIGHT_INK)
    if not in_rounded_rect(px, py, *RECT):
        return (0, 0, 0, 0)
    if in_rounded_rect(px, py, *BLOCK):
        return ink
    if dist_to_poly(px, py) <= STROKE_W / 2:
        return ink
    return bg


def render(size, dark):
    scale = size / VIEW
    n = int(VIEW * SS)
    grid = [[sample((i + 0.5) / SS, (j + 0.5) / SS, dark) for i in range(n)] for j in range(n)]
    per = n // size
    rows = []
    for j in range(size):
        row = bytearray()
        for i in range(size):
            acc = [0, 0, 0, 0]
            for dj in range(per):
                for di in range(per):
                    p = grid[j * per + dj][i * per + di]
                    for k in range(4):
                        acc[k] += p[k]
            m = per * per
            if acc[3] == 0:
                row += bytes(4)
                continue
            # un-premultiply alpha from coverage
            a = acc[3] / m
            row += bytes([min(255, int(acc[c] / m / a + 0.5)) for c in range(3)]
                         + [min(255, int(a * 255 + 0.5))])
        rows.append(bytes(row))
    return zlib.compress(b"".join(b"\x00" + r for r in rows))


def png(size, dark):
    raw = render(size, dark)
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", raw)
            + chunk(b"IEND", b""))


if __name__ == "__main__":
    out = Path("app/static")
    out.mkdir(parents=True, exist_ok=True)
    (out / "favicon-32.png").write_bytes(png(32, True))
    (out / "favicon-light-32.png").write_bytes(png(32, False))
    (out / "apple-touch-icon.png").write_bytes(png(180, True))
    print("wrote favicon-32.png, favicon-light-32.png, apple-touch-icon.png")
