"""Stream capture to the SD card.

mpv's ``stream-record`` property writes the incoming stream to disk while it
keeps playing, so recording costs no extra bandwidth and no second connection.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

# Containers we can write directly; anything else is muxed into Matroska,
# which accepts essentially any radio codec.
_CODEC_EXTENSIONS = {
    "MP3": ".mp3",
    "AAC": ".aac",
    "AAC+": ".aac",
    "AACP": ".aac",
    "OGG": ".ogg",
    "VORBIS": ".ogg",
    "OPUS": ".opus",
    "FLAC": ".flac",
}

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def slugify(value: str, fallback: str = "station") -> str:
    slug = _UNSAFE.sub("-", (value or "").strip()).strip("-")
    return slug[:48] or fallback


def extension_for(codec: str) -> str:
    return _CODEC_EXTENSIONS.get((codec or "").strip().upper(), ".mkv")


class Recorder:
    """Chooses filenames and drives the player's recording hooks."""

    def __init__(self, settings):
        self.settings = settings
        self.last_path: Path | None = None
        self.error: str = ""

    @property
    def directory(self) -> Path:
        return self.settings.recordings_dir

    def build_path(self, station, when: datetime | None = None) -> Path:
        when = when or datetime.now()
        stamp = when.strftime("%Y-%m-%d_%H%M%S")
        name = slugify(getattr(station, "name", ""))
        suffix = extension_for(getattr(station, "codec", ""))
        return self.directory / f"{stamp}_{name}{suffix}"

    def start(self, player, station) -> bool:
        self.error = ""
        path = self.build_path(station)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.error = f"cannot write to {path.parent}: {exc.strerror or exc}"
            return False
        if not player.start_recording(str(path)):
            self.error = "nothing is playing"
            return False
        self.last_path = path
        return True

    def stop(self, player) -> None:
        player.stop_recording()

    def toggle(self, player, station) -> tuple[bool, str]:
        """Returns ``(recording_now, message)``."""
        if player.recording:
            self.stop(player)
            where = self.last_path.name if self.last_path else "file"
            return False, f"saved {where}"
        if station is None:
            return False, "nothing is playing"
        if self.start(player, station):
            return True, f"recording -> {self.last_path.name}"
        return False, self.error or "recording failed"
