#!/usr/bin/env python3
"""Generate Caudal's app icon and the whole macOS icon set.

The mark: one source, three strands leaving it and running out in parallel.
That is what the app does — a single machine feeding several destinations — and
it is what the name means. A *caudal* is the volume a river carries, and in
Spanish it is also the ordinary word for bandwidth.

Everything is drawn from signed distance fields and antialiased analytically,
so each size is rendered at its own resolution rather than downsampled from a
master. That matters at 16 and 32 pixels, where a resampled curve turns to
mush; an SDF stays crisp because the geometry is re-evaluated per pixel.

No image library is available, so PNG and ICNS are assembled by hand from zlib
and struct. Run it after changing anything here:

    python3 tools/icon.py
"""
import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src-tauri" / "icons"

# Three tones of one family. Distinct enough to read as separate strands,
# close enough that the mark is one object rather than three brand colours.
FLOW = [(74, 158, 255), (86, 200, 230), (124, 132, 245)]
SOURCE = (240, 245, 251)
BG_TOP = (17, 21, 31)
BG_BOT = (9, 11, 17)


# ------------------------------------------------------------------ geometry --

def sd_segment(p, a, b):
    pax, pay = p[0] - a[0], p[1] - a[1]
    bax, bay = b[0] - a[0], b[1] - a[1]
    d = bax * bax + bay * bay
    h = 0.0 if d == 0 else max(0.0, min(1.0, (pax * bax + pay * bay) / d))
    return math.hypot(pax - bax * h, pay - bay * h)


def bezier_points(p0, p1, p2, p3, n):
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        x = u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0]
        y = u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


class Stroke:
    """A sampled curve, bucketed by pixel row.

    Testing every segment for every pixel is O(segments) per pixel, which at
    1024px is half a billion distance evaluations and takes hours in Python.
    The curve is monotonic in y, so a given row can only be near a handful of
    segments; bucketing by row turns the inner loop into two or three tests.
    """

    def __init__(self, pts, w, height):
        self.pts = pts
        self.w = w
        self.rows = [[] for _ in range(height)]
        reach = w / 2 + 1.5  # half the stroke, plus a pixel of antialiasing
        for i in range(len(pts) - 1):
            (_, y0), (_, y1) = pts[i], pts[i + 1]
            lo = max(0, int(min(y0, y1) - reach))
            hi = min(height - 1, int(max(y0, y1) + reach))
            for row in range(lo, hi + 1):
                self.rows[row].append(i)

    def __call__(self, p):
        best = 1e9
        for i in self.rows[int(p[1])]:
            d = sd_segment(p, self.pts[i], self.pts[i + 1])
            if d < best:
                best = d
        return best - self.w / 2


def sd_circle(p, c, r):
    return math.hypot(p[0] - c[0], p[1] - c[1]) - r


def sd_squircle(p, c, half, n=5.0):
    dx = abs(p[0] - c[0]) / half
    dy = abs(p[1] - c[1]) / half
    return ((dx**n + dy**n) ** (1.0 / n) - 1.0) * half


def lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


# ------------------------------------------------------------------- the mark --

def mark(S):
    """Shapes for a canvas of S pixels, as (distance function, colour) pairs.

    Strokes get proportionally heavier as the canvas shrinks. A stroke that
    reads as elegant at 512px disappears at 16px, and an icon that vanishes in
    the Dock is a worse failure than one that looks slightly heavy up close.
    """
    boost = 1.0 + max(0.0, (128 - S) / 128) * 0.75
    stroke = S * 0.052 * boost
    spread = S * 0.288
    src = (S * 0.5, S * 0.815)
    top = S * 0.185

    shapes = []
    for dx, col in zip((-spread, 0.0, spread), FLOW):
        tip = (S * 0.5 + dx, top)
        # Leave the source vertically and arrive at the tip vertically, so the
        # curve absorbs all of the turning and no corner is ever visible.
        p1 = (src[0], src[1] - S * 0.30)
        p2 = (tip[0], tip[1] + S * 0.30)
        pts = bezier_points(src, p1, p2, tip, n=max(24, S // 6))
        shapes.append((Stroke(pts, stroke, S), col))
        shapes.append((lambda p, t=tip, r=stroke * 0.92: sd_circle(p, t, r), col))

    shapes.append((lambda p: sd_circle(p, src, S * 0.078 * boost), SOURCE))
    return shapes


def render(S):
    px = bytearray(S * S * 4)
    c = (S / 2.0, S / 2.0)
    half = S / 2.0 - S * 0.055
    shapes = mark(S)

    for y in range(S):
        base = lerp(BG_TOP, BG_BOT, y / max(1, S - 1))
        for x in range(S):
            p = (x + 0.5, y + 0.5)
            a_bg = max(0.0, min(1.0, 0.5 - sd_squircle(p, c, half)))
            if a_bg <= 0.0:
                continue

            best, near, inside = 1e9, None, None
            for sdf, col in shapes:
                d = sdf(p)
                if d < best:
                    best, near = d, col
                # Painter's order: the last shape containing the pixel owns it.
                # Picking the *nearest* shape instead bites chunks out of the
                # dots where a strand passes close by.
                if d <= 0.0:
                    inside = col

            a_fg = min(max(0.0, min(1.0, 0.5 - best)), a_bg)
            t = 0.0 if a_bg == 0 else a_fg / a_bg
            r, g, b = lerp(base, inside or near, t)
            i = (y * S + x) * 4
            px[i], px[i + 1], px[i + 2] = round(r), round(g), round(b)
            px[i + 3] = round(a_bg * 255)
    return bytes(px)


# ------------------------------------------------------------------ encoding --

def png(size, rgba):
    raw = bytearray()
    stride = size * 4
    for y in range(size):
        raw.append(0)  # filter type 0
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        out = struct.pack(">I", len(data)) + tag + data
        return out + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


# Each ICNS slot and the pixel size it expects. Retina slots repeat a size that
# is already present at a different logical scale, so the renders are shared.
ICNS_SLOTS = [
    (b"icp4", 16),
    (b"icp5", 32),
    (b"ic11", 32),    # 16@2x
    (b"ic12", 64),    # 32@2x
    (b"ic07", 128),
    (b"ic13", 256),   # 128@2x
    (b"ic08", 256),
    (b"ic14", 512),   # 256@2x
    (b"ic09", 512),
    (b"ic10", 1024),  # 512@2x
]


def icns(pngs):
    body = b"".join(
        tag + struct.pack(">I", len(pngs[size]) + 8) + pngs[size]
        for tag, size in ICNS_SLOTS
    )
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = sorted({size for _, size in ICNS_SLOTS} | {107, 142, 512})

    pngs = {}
    for size in sizes:
        pngs[size] = png(size, render(size))
        print(f"  rendered {size}×{size}")

    # The names in tauri.conf.json, plus the Windows-style squares Tauri's
    # scaffold leaves behind. Cheap to keep, confusing to half-remove.
    for name, size in [
        ("32x32.png", 32),
        ("128x128.png", 128),
        ("128x128@2x.png", 256),
        ("icon.png", 512),
        ("Square107x107Logo.png", 107),
        ("Square142x142Logo.png", 142),
    ]:
        (OUT / name).write_bytes(pngs[size])

    (OUT / "icon.icns").write_bytes(icns(pngs))
    print(f"  wrote {len(sizes)} sizes and icon.icns to {OUT}")


if __name__ == "__main__":
    main()
