"""Palette, geometry and font loading for the CardputerZero 320x170 panel.

The panel is small and glossy, so the palette is high-contrast on a near-black
ground.  Every colour is an ``#rrggbb`` string because that is what PIL's
ImageDraw accepts directly.
"""

from __future__ import annotations

from functools import lru_cache

# The CP0 LCD is a 1.9" ST7789 panel in landscape.
WIDTH = 320
HEIGHT = 170

# Chrome
BG = "#0b0d10"
PANEL = "#151a21"
PANEL_HI = "#1d242e"
LINE = "#2a323d"

# Type
TEXT = "#e8edf2"
MUTED = "#7d8794"
DIM = "#4a5563"

# Semantic
ACCENT = "#f2a03d"  # amber - primary highlight, selection
COOL = "#4fc3f7"  # cyan - links, metadata
OK = "#58d68d"
WARN = "#f2c14e"
ERR = "#e05c5c"
REC = "#ff4d4d"

# Layout
HEADER_H = 18
FOOTER_H = 14
BODY_TOP = HEADER_H + 3
BODY_BOTTOM = HEIGHT - FOOTER_H - 3
PAD = 6

# Font candidates, best first.  DejaVu ships with fonts-dejavu-core; the
# Alibaba face is what the stock CardputerZero apps bundle, so it is a
# reasonable second choice on a stock image.
_FONT_CANDIDATES = {
    False: (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/APPLaunch/share/font/AlibabaPuHuiTi-3-55-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ),
    True: (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/APPLaunch/share/font/AlibabaPuHuiTi-3-55-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
}

# Extra directories searched when the absolute paths above are missing, so the
# app still renders real glyphs on a non-Debian dev box.
_FONT_DIRS = (
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    "/Library/Fonts",
    "C:/Windows/Fonts",
)


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False):
    """Return a PIL font of ``size`` px, falling back to the bitmap default."""
    from PIL import ImageFont

    for path in _FONT_CANDIDATES[bool(bold)]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue

    for found in _scan_font_dirs(bool(bold)):
        try:
            return ImageFont.truetype(found, size)
        except OSError:
            continue

    return ImageFont.load_default()


@lru_cache(maxsize=2)
def _scan_font_dirs(bold: bool) -> tuple[str, ...]:
    """Best-effort search for a monospace TTF anywhere on the system."""
    import os

    wanted_bold = "bold" if bold else ""
    hits: list[str] = []
    for root in _FONT_DIRS:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                low = name.lower()
                if not low.endswith(".ttf"):
                    continue
                if "mono" not in low:
                    continue
                if wanted_bold and "bold" not in low:
                    continue
                if not wanted_bold and "bold" in low:
                    continue
                hits.append(os.path.join(dirpath, name))
            if len(hits) > 8:
                break
    return tuple(sorted(hits))
