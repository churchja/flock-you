#!/usr/bin/env python3
"""Generate the launcher icon.

Kept as a script (rather than a committed binary nobody can diff) so the icon
can be regenerated whenever the palette changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from cpzradio import theme  # noqa: E402

SIZE = 128


def build() -> Image.Image:
    # Supersample, then downscale, so the curves come out smooth.
    scale = 4
    side = SIZE * scale
    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        (0, 0, side - 1, side - 1), radius=24 * scale, fill=theme.PANEL, outline=theme.LINE, width=2 * scale
    )

    # Broadcast arcs radiating from the antenna base.
    origin = (side * 0.30, side * 0.66)
    for index, radius in enumerate((0.20, 0.31, 0.42)):
        r = side * radius
        box = (origin[0] - r, origin[1] - r, origin[0] + r, origin[1] + r)
        colour = (theme.ACCENT, theme.ACCENT, theme.COOL)[index]
        draw.arc(box, start=248, end=310, fill=colour, width=int(5 * scale))

    # Antenna mast and emitter.
    draw.line(
        (origin[0], origin[1], origin[0] + side * 0.30, origin[1] - side * 0.34),
        fill=theme.TEXT,
        width=int(4 * scale),
    )
    draw.ellipse(
        (origin[0] - 7 * scale, origin[1] - 7 * scale, origin[0] + 7 * scale, origin[1] + 7 * scale),
        fill=theme.ACCENT,
    )

    # Tuning dial along the base.
    base_y = side * 0.83
    draw.line((side * 0.16, base_y, side * 0.84, base_y), fill=theme.DIM, width=int(3 * scale))
    for step in range(9):
        x = side * 0.16 + (side * 0.68) * step / 8
        height = 9 * scale if step % 4 else 15 * scale
        draw.line((x, base_y - height, x, base_y), fill=theme.MUTED, width=int(2 * scale))
    marker_x = side * 0.16 + (side * 0.68) * 0.62
    draw.line((marker_x, base_y - 19 * scale, marker_x, base_y + 4 * scale), fill=theme.REC, width=int(3 * scale))

    return image.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "icon.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    build().save(target)
    print(f"wrote {target} ({SIZE}x{SIZE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
