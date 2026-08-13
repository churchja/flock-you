"""Filesystem layout and persisted settings.

Config lives under ``~/.config/cardputerzero-radio`` and mutable state under
``~/.local/share/cardputerzero-radio``, both overridable with environment
variables so tests never touch a real home directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

APP_ID = "cardputerzero-radio"
APP_NAME = "CardputerZero Radio"
VERSION = "0.1.0"
USER_AGENT = f"{APP_ID}/{VERSION}"

# Where the .deb installs the app payload.
INSTALL_ROOT = Path("/usr/share/APPLaunch/apps") / APP_ID


def config_dir() -> Path:
    override = os.environ.get("CPZRADIO_CONFIG_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / APP_ID


def data_dir() -> Path:
    override = os.environ.get("CPZRADIO_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP_ID


def bundled_stations() -> Path:
    """The read-only default station list shipped inside the package."""
    local = Path(__file__).resolve().parent.parent / "data" / "stations.toml"
    if local.is_file():
        return local
    return INSTALL_ROOT / "data" / "stations.toml"


def write_atomic(path: Path, text: str) -> None:
    """Write via a temp file + rename so a power cut cannot truncate state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, value) -> None:
    write_atomic(path, json.dumps(value, indent=2, sort_keys=True))


DEFAULT_SETTINGS = {
    "volume": 70,
    "last_url": "",
    "favorites": [],
    "audio_device": "",
    "sleep_minutes": 30,
    "recordings_dir": "",
    "alarm": {"enabled": False, "hour": 7, "minute": 0, "url": "", "days": [0, 1, 2, 3, 4]},
}


class Settings:
    """A small JSON-backed settings bag with attribute-ish access."""

    def __init__(self, path: Path | None = None):
        self.path = path or (config_dir() / "settings.json")
        stored = read_json(self.path, {})
        self.values = {**DEFAULT_SETTINGS}
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key in self.values:
                    self.values[key] = value
        # Merge alarm sub-keys so an old file missing a field still works.
        alarm = {**DEFAULT_SETTINGS["alarm"]}
        if isinstance(self.values.get("alarm"), dict):
            alarm.update(self.values["alarm"])
        self.values["alarm"] = alarm

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    def set(self, key: str, value) -> None:
        self.values[key] = value

    def save(self) -> None:
        write_json(self.path, self.values)

    @property
    def recordings_dir(self) -> Path:
        configured = (self.values.get("recordings_dir") or "").strip()
        if configured:
            return Path(configured).expanduser()
        return Path.home() / "Music" / "radio-recordings"
