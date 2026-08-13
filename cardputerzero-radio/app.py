#!/usr/bin/env python3
"""Entry point for CardputerZero Radio.

On the device this is launched by APPLaunch and picks the framebuffer backend
automatically.  On a desktop it falls back to the Tk simulator, so the whole UI
can be developed without hardware.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from cpzradio import config, runtime, ui  # noqa: E402
from cpzradio.radio import RadioApp  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=config.APP_ID, description=__doc__)
    backend = parser.add_mutually_exclusive_group()
    backend.add_argument("--desktop", action="store_true", help="force the Tk simulator")
    backend.add_argument("--fb", action="store_true", help="force the framebuffer backend")
    parser.add_argument("--scale", type=int, default=3, help="simulator zoom factor (default 3)")
    parser.add_argument("--fps", type=int, default=20, help="render loop rate (default 20)")
    parser.add_argument("--no-autoplay", action="store_true", help="do not resume the last station")
    parser.add_argument(
        "--smoke",
        nargs="?",
        const="",
        metavar="DIR",
        help="render every screen once (optionally to PNGs) and exit",
    )
    parser.add_argument("--version", action="version", version=f"{config.APP_ID} {config.VERSION}")
    return parser


def run_smoke(outdir: str) -> int:
    """Render each screen once.  Never starts mpv, so it is safe in CI."""
    from cpzradio.library import Station

    app = RadioApp(autoplay=False)
    backend = runtime.HeadlessBackend(outdir or None)

    # Give the search screen something to draw.
    app.search_results = [
        Station(name="Example FM", url="http://example.invalid/1", genre="jazz", country="US", bitrate=128),
        Station(name="Another Station With A Long Name", url="http://example.invalid/2", genre="ambient", bitrate=64),
    ]
    app.query = "example"
    app._searched_query = "example"

    def capture(label: str) -> None:
        nonlocal rendered
        app.update()
        image = app.draw()
        if image.size != (ui.theme.WIDTH, ui.theme.HEIGHT):
            raise SystemExit(f"{label}: unexpected frame size {image.size}")
        if not image.getbbox():
            raise SystemExit(f"{label}: rendered an empty frame")
        backend.present(image)
        rendered += 1

    rendered = 0
    for screen in ("now", "stations", "search", "settings"):
        app.screen = screen
        capture(screen)

    # A live-playback frame exercises the marquee, the status chips and the
    # ICY metadata path, none of which the idle frames touch.
    station = app.current_station
    if station is not None:
        app.player.url = station.url
        app.player.station_name = station.name
        app.player.state = "playing"
        app.player._props["metadata"] = {
            "icy-title": "An Unusually Long Track Title That Has To Scroll — Artist Name"
        }
        app.player._props["demuxer-cache-time"] = 22.0
        app.player.recording_path = "/tmp/example.mp3"
        app.sleep.start(30)
        app.alarm.config.enabled = True
        app.screen = "now"
        capture("now-playing")

    app.player.shutdown()
    where = f" -> {outdir}" if outdir else ""
    print(f"smoke ok: {rendered} screens rendered at {ui.theme.WIDTH}x{ui.theme.HEIGHT}{where}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.smoke is not None:
        return run_smoke(args.smoke)

    prefer = "fb" if args.fb else "tk" if args.desktop else "auto"
    try:
        backend = runtime.pick_backend(prefer, scale=args.scale, title=config.APP_NAME)
    except Exception as exc:  # noqa: BLE001 - a clear message beats a traceback here
        print(f"{config.APP_ID}: cannot open a display backend: {exc}", file=sys.stderr)
        return 1

    app = RadioApp(autoplay=not args.no_autoplay)
    return runtime.run(app, backend, fps=args.fps)


if __name__ == "__main__":
    raise SystemExit(main())
