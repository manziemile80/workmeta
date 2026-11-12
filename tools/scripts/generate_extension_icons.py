"""Generate the agent's icons.

The agent ships with one icon in four sizes. They are drawn here rather than
committed as binaries so the brand colours stay in one place: the navy tile
with the teal "W" is the WorkMeta AI mark.

Usage (from the repository root)::

    apps/api/.venv/Scripts/python tools/scripts/generate_extension_icons.py

The PNGs are written to ``apps/extension/public/icons/``.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "apps" / "extension" / "public" / "icons"

SIZES = (16, 32, 48, 128)

NAVY = (11, 18, 32)
TEAL = (45, 212, 191)

# The "W" is four strokes. Each is a line segment in unit coordinates.
STROKES = (
    ((0.20, 0.28), (0.35, 0.72)),
    ((0.35, 0.72), (0.50, 0.40)),
    ((0.50, 0.40), (0.65, 0.72)),
    ((0.65, 0.72), (0.80, 0.28)),
)


def _distance_to_segment(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    """The distance from a point to a line segment."""
    dx = bx - ax
    dy = by - ay
    length = dx * dx + dy * dy
    if length == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _rounded_square_coverage(x: float, y: float, size: int, radius: float) -> float:
    """How much of the pixel lies inside the rounded tile."""
    half = size / 2
    dx = abs(x - half) - (half - radius)
    dy = abs(y - half) - (half - radius)
    if dx <= 0 and dy <= 0:
        return 1.0
    return math.hypot(max(dx, 0.0), max(dy, 0.0)) <= radius


def _render(size: int) -> bytes:
    """Render the icon and return its raw RGBA rows."""
    samples = 4
    radius = size * 0.22
    stroke_width = size * 0.085
    rows = bytearray()
    for py in range(size):
        rows.append(0)  # PNG filter type 0 (none) for this row.
        for px in range(size):
            tile = 0
            mark = 0
            for sy in range(samples):
                for sx in range(samples):
                    x = px + (sx + 0.5) / samples
                    y = py + (sy + 0.5) / samples
                    if _rounded_square_coverage(x, y, size, radius):
                        tile += 1
                    ux = x / size
                    uy = y / size
                    if any(
                        _distance_to_segment(ux, uy, start[0], start[1], end[0], end[1])
                        <= stroke_width / size
                        for start, end in STROKES
                    ):
                        mark += 1
            total = samples * samples
            tile_alpha = tile / total
            mark_alpha = mark / total

            # The mark is drawn over the tile, so blend it in.
            r = round(NAVY[0] * (1 - mark_alpha) + TEAL[0] * mark_alpha)
            g = round(NAVY[1] * (1 - mark_alpha) + TEAL[1] * mark_alpha)
            b = round(NAVY[2] * (1 - mark_alpha) + TEAL[2] * mark_alpha)
            rows.extend((r, g, b, round(255 * tile_alpha)))
    return bytes(rows)


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _png(size: int, raw: bytes) -> bytes:
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        path = OUT_DIR / f"icon-{size}.png"
        path.write_bytes(_png(size, _render(size)))
        print(f"Wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
