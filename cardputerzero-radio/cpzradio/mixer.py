"""Volume control.

Prefers the real ALSA mixer so the level survives app restarts and matches the
rest of the system; falls back to mpv's software volume when no usable playback
control exists (common when audio is routed through PipeWire/PulseAudio).
"""

from __future__ import annotations

import re
import shutil
import subprocess

# Ordered by preference; the CP0 exposes a 1W speaker plus a 3.5mm jack, and
# which control is authoritative depends on the image's ALSA config.
PREFERRED_CONTROLS = ("Master", "PCM", "Speaker", "Headphone", "Digital", "Playback")

_PERCENT = re.compile(r"\[(\d{1,3})%\]")


def _run(args: list[str], timeout: float = 3.0) -> str:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


class AlsaMixer:
    """Thin amixer wrapper.  ``control`` is None when nothing usable was found."""

    def __init__(self, control: str | None = None):
        self.control = control or self._detect()

    @staticmethod
    def available() -> bool:
        return shutil.which("amixer") is not None

    @classmethod
    def _detect(cls) -> str | None:
        if not cls.available():
            return None
        listing = _run(["amixer", "scontrols"])
        found = re.findall(r"'([^']+)'", listing)
        for name in PREFERRED_CONTROLS:
            if name in found:
                return name
        # Otherwise take the first control that actually has a playback volume.
        for name in found:
            if _PERCENT.search(_run(["amixer", "sget", name])):
                return name
        return None

    def get(self) -> int | None:
        if not self.control:
            return None
        match = _PERCENT.search(_run(["amixer", "sget", self.control]))
        return int(match.group(1)) if match else None

    def set(self, percent: int) -> bool:
        if not self.control:
            return False
        percent = max(0, min(100, int(percent)))
        # -M applies a perceptual (mapped) curve, which feels far better than
        # raw linear steps on a small speaker.
        return bool(_run(["amixer", "-M", "sset", self.control, f"{percent}%"]))


class VolumeControl:
    """Single knob the UI talks to, backed by ALSA or by mpv."""

    STEP = 5

    def __init__(self, player, settings):
        self.player = player
        self.settings = settings
        self.mixer = AlsaMixer()
        self.level = int(settings.get("volume", 70))

        if self.mixer.control:
            current = self.mixer.get()
            if current is not None:
                self.level = current
            # mpv stays at full scale so the hardware mixer is the only taper.
            self.player.set_volume(100)
            self.mixer.set(self.level)
        else:
            self.player.set_volume(self.level)

    @property
    def backend(self) -> str:
        return f"alsa:{self.mixer.control}" if self.mixer.control else "mpv"

    def set(self, level: int) -> int:
        self.level = max(0, min(100, int(level)))
        if self.mixer.control:
            self.mixer.set(self.level)
        else:
            self.player.set_volume(self.level)
        self.settings.set("volume", self.level)
        return self.level

    def nudge(self, delta: int) -> int:
        return self.set(self.level + delta)

    def up(self) -> int:
        return self.nudge(self.STEP)

    def down(self) -> int:
        return self.nudge(-self.STEP)
