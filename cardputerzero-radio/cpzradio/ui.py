"""Rendering: a small painter over PIL plus one function per screen.

Everything draws into a 320x170 RGB image.  Screen functions are pure in the
sense that they only read app state - all mutation happens in the key handlers.
"""

from __future__ import annotations

from datetime import datetime

from . import theme
from .player import CONNECTING, ERROR, PLAYING, STOPPED

STATE_COLORS = {
    PLAYING: theme.OK,
    CONNECTING: theme.WARN,
    ERROR: theme.ERR,
    STOPPED: theme.MUTED,
}

STATE_LABELS = {
    PLAYING: "ON AIR",
    CONNECTING: "TUNING",
    ERROR: "ERROR",
    STOPPED: "STOPPED",
}


class Painter:
    """Draw helpers bound to one frame."""

    def __init__(self, image):
        from PIL import ImageDraw

        self.image = image
        self.draw = ImageDraw.Draw(image)

    # -- primitives --------------------------------------------------------

    def clear(self, color: str = theme.BG) -> None:
        self.draw.rectangle((0, 0, theme.WIDTH, theme.HEIGHT), fill=color)

    def rect(self, box, fill=None, outline=None, width: int = 1, radius: int = 0):
        if radius:
            self.draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
        else:
            self.draw.rectangle(box, fill=fill, outline=outline, width=width)

    def hline(self, x1: int, y: int, x2: int, color: str = theme.LINE) -> None:
        self.draw.line((x1, y, x2, y), fill=color)

    def text(self, x, y, value, fill=theme.TEXT, size=10, bold=False, anchor="la"):
        self.draw.text((x, y), str(value), font=theme.font(size, bold), fill=fill, anchor=anchor)

    def width_of(self, value: str, size: int = 10, bold: bool = False) -> float:
        return self.draw.textlength(str(value), font=theme.font(size, bold))

    def ellipsize(self, value: str, max_px: float, size: int = 10, bold: bool = False) -> str:
        value = str(value)
        if self.width_of(value, size, bold) <= max_px:
            return value
        ellipsis = "…"
        while value and self.width_of(value + ellipsis, size, bold) > max_px:
            value = value[:-1]
        return value + ellipsis

    def marquee(self, x, y, max_px, value, phase, fill=theme.TEXT, size=10, bold=False, speed=24):
        """Draw text, scrolling it horizontally when it does not fit.

        Rendered into a scratch tile and pasted so it cannot bleed past
        ``max_px`` - PIL's ImageDraw has no clip region.
        """
        from PIL import Image

        value = str(value)
        text_px = self.width_of(value, size, bold)
        if text_px <= max_px:
            self.text(x, y, value, fill, size, bold)
            return

        gap = "     ·     "
        loop = value + gap
        loop_px = int(self.width_of(loop, size, bold)) or 1
        height = int(size * 1.6) + 2
        tile = Image.new("RGB", (int(max_px), height), theme.BG)

        from PIL import ImageDraw

        tile_draw = ImageDraw.Draw(tile)
        font = theme.font(size, bold)
        offset = int(phase * speed) % loop_px
        tile_draw.text((-offset, 0), loop, font=font, fill=fill, anchor="la")
        tile_draw.text((loop_px - offset, 0), loop, font=font, fill=fill, anchor="la")
        self.image.paste(tile, (int(x), int(y)))

    # -- chrome ------------------------------------------------------------

    def header(self, title: str, right: str = "") -> None:
        self.rect((0, 0, theme.WIDTH, theme.HEADER_H), fill=theme.PANEL)
        self.text(theme.PAD, 4, title, theme.TEXT, 11, True)
        stamp = right or datetime.now().strftime("%H:%M")
        self.text(theme.WIDTH - theme.PAD, 4, stamp, theme.MUTED, 10, anchor="ra")
        self.hline(0, theme.HEADER_H, theme.WIDTH, theme.LINE)

    def footer(self, hints: str, notice: str = "", notice_color: str = theme.ACCENT) -> None:
        top = theme.HEIGHT - theme.FOOTER_H
        self.hline(0, top - 1, theme.WIDTH, theme.LINE)
        if notice:
            self.rect((0, top, theme.WIDTH, theme.HEIGHT), fill=theme.PANEL_HI)
            self.text(theme.PAD, top + 2, self.ellipsize(notice, theme.WIDTH - 2 * theme.PAD, 9), notice_color, 9)
            return
        self.text(theme.PAD, top + 2, self.ellipsize(hints, theme.WIDTH - 2 * theme.PAD, 9), theme.DIM, 9)

    def scrollbar(self, top: int, bottom: int, total: int, visible: int, first: int) -> None:
        if total <= visible:
            return
        x = theme.WIDTH - 3
        track_h = bottom - top
        self.draw.line((x, top, x, bottom), fill=theme.LINE)
        thumb_h = max(8, int(track_h * visible / total))
        span = track_h - thumb_h
        travel = int(span * first / max(1, total - visible))
        self.draw.line((x, top + travel, x, top + travel + thumb_h), fill=theme.DIM, width=2)

    def volume_bar(self, x, y, width, level, label="VOL") -> None:
        self.text(x, y - 1, label, theme.MUTED, 9)
        bar_x = x + 26
        bar_w = width - 26 - 30
        self.rect((bar_x, y, bar_x + bar_w, y + 8), outline=theme.LINE)
        filled = int(bar_w * max(0, min(100, level)) / 100)
        if filled > 1:
            self.rect((bar_x + 1, y + 1, bar_x + filled - 1, y + 7), fill=theme.ACCENT)
        self.text(x + width, y - 1, f"{level:3d}", theme.TEXT, 9, anchor="ra")


def _window(count: int, selected: int, rows: int) -> int:
    """First visible index that keeps ``selected`` on screen."""
    if count <= rows:
        return 0
    first = selected - rows // 2
    return max(0, min(first, count - rows))


# ---------------------------------------------------------------------------
# screens
# ---------------------------------------------------------------------------


def draw_now_playing(painter: Painter, app) -> None:
    player = app.player
    station = app.current_station
    painter.header("RADIO")

    name = station.name if station else "No station"
    painter.text(theme.PAD, 24, painter.ellipsize(name, theme.WIDTH - 2 * theme.PAD, 15, True), theme.TEXT, 15, True)

    subtitle = station.subtitle if station else "press S to browse stations"
    painter.text(theme.PAD, 45, painter.ellipsize(subtitle, theme.WIDTH - 2 * theme.PAD, 9), theme.MUTED, 9)

    # ICY metadata, scrolled when long.
    title = player.now_playing
    if title:
        painter.marquee(theme.PAD, 60, theme.WIDTH - 2 * theme.PAD, title, app.phase, theme.COOL, 11)
    elif player.state == PLAYING:
        painter.text(theme.PAD, 60, "no track metadata", theme.DIM, 10)
    elif station is not None:
        painter.text(theme.PAD, 60, "SPACE to play", theme.DIM, 10)

    state = player.state
    color = STATE_COLORS.get(state, theme.MUTED)
    label = STATE_LABELS.get(state, state.upper())
    if state == ERROR and player.error_text:
        label = f"{label} · {player.error_text}"
    painter.rect((theme.PAD, 84, theme.PAD + 8, 92), fill=color)
    painter.text(theme.PAD + 14, 83, painter.ellipsize(label, 200, 10, True), color, 10, True)

    if state == PLAYING and player.buffered_seconds:
        painter.text(theme.WIDTH - theme.PAD, 83, f"buf {player.buffered_seconds:.0f}s", theme.DIM, 9, anchor="ra")

    painter.volume_bar(theme.PAD, 104, theme.WIDTH - 2 * theme.PAD, app.volume.level)

    # Status chips
    chips = []
    if app.sleep.active:
        chips.append(("SLEEP " + app.sleep.label(), theme.WARN))
    if app.alarm.config.enabled:
        chips.append(("ALARM " + app.alarm.config.clock, theme.COOL))
    if player.recording:
        chips.append(("REC", theme.REC))
    x = theme.PAD
    for text_value, chip_color in chips:
        width = int(painter.width_of(text_value, 9)) + 10
        painter.rect((x, 122, x + width, 136), outline=chip_color, radius=3)
        painter.text(x + 5, 125, text_value, chip_color, 9)
        x += width + 6

    painter.footer("SPACE play  UP/DN tune  L/R vol  S list  / find  G setup")


def draw_stations(painter: Painter, app) -> None:
    stations = app.visible_stations
    painter.header("STATIONS", f"{len(stations)}")

    rows = 5
    row_h = 24
    top = theme.BODY_TOP + 2
    if not stations:
        painter.text(theme.PAD, 60, "No stations.", theme.MUTED, 11)
        painter.text(theme.PAD, 76, "Press / to search Radio Browser,", theme.DIM, 9)
        painter.text(theme.PAD, 88, "or edit stations.toml on the device.", theme.DIM, 9)
        painter.footer("/ search   F favourites filter   ESC back")
        return

    first = _window(len(stations), app.station_index, rows)
    for offset in range(min(rows, len(stations) - first)):
        index = first + offset
        station = stations[index]
        y = top + offset * row_h
        selected = index == app.station_index
        if selected:
            painter.rect((2, y, theme.WIDTH - 6, y + row_h - 3), fill=theme.PANEL_HI, radius=3)

        live = app.player.url == station.url and app.player.state in (PLAYING, CONNECTING)
        marker_color = STATE_COLORS.get(app.player.state, theme.OK) if live else theme.DIM
        if live:
            painter.rect((6, y + 7, 10, y + 14), fill=marker_color)

        star_x = 15
        if station.favorite:
            painter.text(star_x, y + 3, "★", theme.ACCENT, 10)

        name_x = star_x + 12
        name_px = theme.WIDTH - name_x - 14
        painter.text(
            name_x, y + 2,
            painter.ellipsize(station.name, name_px, 11, selected),
            theme.TEXT if selected else theme.TEXT, 11, selected,
        )
        if station.subtitle:
            painter.text(
                name_x, y + 13,
                painter.ellipsize(station.subtitle, name_px, 8),
                theme.MUTED if selected else theme.DIM, 8,
            )

    painter.scrollbar(top, theme.BODY_BOTTOM, len(stations), rows, first)
    mode = "favourites" if app.favorites_only else "all"
    painter.footer(f"ENTER play  F fav  / search  T {mode}  X remove  ESC back")


def draw_search(painter: Painter, app) -> None:
    painter.header("SEARCH")

    # Query line
    painter.rect((theme.PAD, 22, theme.WIDTH - theme.PAD, 40), fill=theme.PANEL, outline=theme.LINE, radius=3)
    cursor = "_" if int(app.phase * 2) % 2 == 0 else " "
    query = app.query or ""
    shown = painter.ellipsize(query, theme.WIDTH - 2 * theme.PAD - 22, 11)
    painter.text(theme.PAD + 6, 26, shown + cursor, theme.TEXT, 11)
    if not query:
        painter.text(theme.PAD + 16, 26, "type a station name", theme.DIM, 10)

    status = app.search_status()
    if status:
        painter.text(theme.PAD, 45, status, theme.MUTED, 9)

    results = app.search_results
    rows = 4
    row_h = 22
    top = 58
    first = _window(len(results), app.search_index, rows)
    for offset in range(min(rows, len(results) - first)):
        index = first + offset
        station = results[index]
        y = top + offset * row_h
        selected = index == app.search_index
        if selected:
            painter.rect((2, y, theme.WIDTH - 6, y + row_h - 3), fill=theme.PANEL_HI, radius=3)
        known = app.library.find(station.url) is not None
        painter.text(6, y + 1, "✓" if known else "+", theme.OK if known else theme.DIM, 10)
        painter.text(
            20, y + 1,
            painter.ellipsize(station.name, theme.WIDTH - 34, 10, selected),
            theme.TEXT, 10, selected,
        )
        painter.text(
            20, y + 11,
            painter.ellipsize(station.subtitle, theme.WIDTH - 34, 8),
            theme.MUTED if selected else theme.DIM, 8,
        )

    if results:
        painter.scrollbar(top, theme.BODY_BOTTOM, len(results), rows, first)
    painter.footer("ENTER search/play  TAB save  UP/DN pick  ESC back")


def draw_settings(painter: Painter, app) -> None:
    painter.header("SETTINGS")
    items = app.settings_rows()
    rows = 6
    row_h = 20
    top = theme.BODY_TOP + 1
    first = _window(len(items), app.settings_index, rows)

    selected_hint = ""
    for offset in range(min(rows, len(items) - first)):
        index = first + offset
        label, value, hint = items[index]
        y = top + offset * row_h
        selected = index == app.settings_index
        if selected:
            painter.rect((2, y, theme.WIDTH - 6, y + row_h - 3), fill=theme.PANEL_HI, radius=3)
            selected_hint = hint
        painter.text(8, y + 4, label, theme.TEXT if selected else theme.MUTED, 10, selected)
        painter.text(
            theme.WIDTH - 12, y + 4,
            painter.ellipsize(value, 168, 10, selected),
            theme.ACCENT if selected else theme.MUTED, 10, selected, anchor="ra",
        )

    painter.scrollbar(top, theme.BODY_BOTTOM, len(items), rows, first)
    # The hint lives in the footer: at 20px per row there is no space for a
    # second line without colliding with the next entry.
    painter.footer(selected_hint or "UP/DN row  L/R change  ENTER toggle  ESC back")


SCREENS = {
    "now": draw_now_playing,
    "stations": draw_stations,
    "search": draw_search,
    "settings": draw_settings,
}


def render(image, app):
    painter = Painter(image)
    painter.clear()
    SCREENS.get(app.screen, draw_now_playing)(painter, app)
    return image
