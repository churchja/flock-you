"""Station storage and Radio Browser lookup.

Three sources feed one list:

* ``stations.toml``   - the curated/user-edited list, never rewritten by the app
* ``saved.json``      - stations kept from a Radio Browser search
* ``favorites``       - a set of URLs stored in settings.json

Search runs on a worker thread so a slow network never stalls the render loop.
"""

from __future__ import annotations

import json
import threading
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .config import USER_AGENT, config_dir, read_json, write_json

# Public Radio Browser mirrors, tried in order if discovery fails.
FALLBACK_SERVERS = (
    "https://de2.api.radio-browser.info",
    "https://nl1.api.radio-browser.info",
    "https://at1.api.radio-browser.info",
)
DISCOVERY_URL = "https://all.api.radio-browser.info/json/servers"

# Playlist wrappers we unwrap ourselves; .m3u8 is HLS and mpv handles it natively.
PLAYLIST_SUFFIXES = (".pls", ".m3u")


@dataclass
class Station:
    name: str
    url: str
    genre: str = ""
    country: str = ""
    codec: str = ""
    bitrate: int = 0
    homepage: str = ""
    uuid: str = ""
    source: str = "local"
    favorite: bool = field(default=False, compare=False)

    @property
    def subtitle(self) -> str:
        bits = [b for b in (self.genre, self.country) if b]
        if self.bitrate:
            bits.append(f"{self.bitrate}k")
        elif self.codec:
            bits.append(self.codec)
        return " · ".join(bits)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "genre": self.genre,
            "country": self.country,
            "codec": self.codec,
            "bitrate": self.bitrate,
            "homepage": self.homepage,
            "uuid": self.uuid,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: dict, source: str = "local") -> "Station":
        return cls(
            name=str(raw.get("name") or "Unnamed").strip(),
            url=str(raw.get("url") or "").strip(),
            genre=str(raw.get("genre") or "").strip(),
            country=str(raw.get("country") or "").strip(),
            codec=str(raw.get("codec") or "").strip(),
            bitrate=int(raw.get("bitrate") or 0),
            homepage=str(raw.get("homepage") or "").strip(),
            uuid=str(raw.get("uuid") or "").strip(),
            source=str(raw.get("source") or source),
        )


def parse_stations_toml(text: str) -> list[Station]:
    """Parse a ``[[station]]`` array from TOML text."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    entries = document.get("station")
    if not isinstance(entries, list):
        return []
    stations = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        station = Station.from_dict(entry, source="local")
        if station.url:
            stations.append(station)
    return stations


def resolve_stream_url(url: str, timeout: float = 6.0) -> str:
    """Unwrap a .pls/.m3u playlist to its first stream URL.

    Returns the original URL unchanged on any failure - mpv can often cope
    with the wrapper itself, so failing soft is better than failing loud.
    """
    path = urllib.parse.urlparse(url).path.lower()
    if not path.endswith(PLAYLIST_SUFFIXES):
        return url
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(65536).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return url

    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("file") and "=" in line:  # .pls
            candidate = line.split("=", 1)[1].strip()
        elif line.startswith(("http://", "https://")):  # .m3u
            candidate = line
        else:
            continue
        if candidate.startswith(("http://", "https://")):
            return candidate
    return url


class RadioBrowser:
    """Minimal Radio Browser client with threaded search."""

    def __init__(self, servers=FALLBACK_SERVERS):
        self.servers = list(servers)
        self._base: str | None = None

    def base_url(self, timeout: float = 5.0) -> str:
        if self._base:
            return self._base
        try:
            request = urllib.request.Request(
                DISCOVERY_URL, headers={"User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                servers = json.loads(response.read().decode("utf-8", "replace"))
            names = [s.get("name") for s in servers if isinstance(s, dict) and s.get("name")]
            if names:
                self._base = f"https://{sorted(names)[0]}"
                return self._base
        except (urllib.error.URLError, OSError, ValueError):
            pass
        self._base = self.servers[0]
        return self._base

    def search(self, query: str, limit: int = 40, timeout: float = 10.0) -> list[Station]:
        params = urllib.parse.urlencode(
            {
                "name": query,
                "limit": limit,
                "hidebroken": "true",
                "order": "clickcount",
                "reverse": "true",
            }
        )
        last_error: Exception | None = None
        for base in [self.base_url()] + self.servers:
            url = f"{base}/json/stations/search?{params}"
            try:
                request = urllib.request.Request(
                    url, headers={"User-Agent": USER_AGENT}
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    payload = json.loads(response.read().decode("utf-8", "replace"))
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last_error = exc
                continue
            return [self._station(item) for item in payload if isinstance(item, dict)]
        raise ConnectionError(f"Radio Browser unreachable: {last_error}")

    @staticmethod
    def _station(item: dict) -> Station:
        return Station(
            name=(item.get("name") or "Unnamed").strip(),
            url=(item.get("url_resolved") or item.get("url") or "").strip(),
            genre=(item.get("tags") or "").split(",")[0].strip(),
            country=(item.get("countrycode") or "").strip(),
            codec=(item.get("codec") or "").strip(),
            bitrate=int(item.get("bitrate") or 0),
            homepage=(item.get("homepage") or "").strip(),
            uuid=(item.get("stationuuid") or "").strip(),
            source="radio-browser",
        )


class SearchJob:
    """Runs one search on a worker thread and exposes its result."""

    def __init__(self, browser: RadioBrowser, query: str, limit: int = 40):
        self.query = query
        self.results: list[Station] = []
        self.error: str = ""
        self.done = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(browser, query, limit), daemon=True
        )
        self._thread.start()

    def _run(self, browser: RadioBrowser, query: str, limit: int) -> None:
        try:
            self.results = [s for s in browser.search(query, limit=limit) if s.url]
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.error = str(exc) or exc.__class__.__name__
        finally:
            self.done.set()

    @property
    def finished(self) -> bool:
        return self.done.is_set()


class Library:
    """The combined station list plus favourites."""

    def __init__(self, settings, stations_path: Path | None = None):
        self.settings = settings
        self.dir = config_dir()
        self.stations_path = stations_path or (self.dir / "stations.toml")
        self.saved_path = self.dir / "saved.json"
        self.local: list[Station] = []
        self.saved: list[Station] = []
        self.reload()

    # -- loading -----------------------------------------------------------

    def ensure_seeded(self, bundled: Path) -> None:
        """Copy the shipped station list into the config dir on first run."""
        if self.stations_path.exists():
            return
        try:
            text = bundled.read_text(encoding="utf-8")
        except OSError:
            return
        self.stations_path.parent.mkdir(parents=True, exist_ok=True)
        self.stations_path.write_text(text, encoding="utf-8")

    def reload(self) -> None:
        try:
            text = self.stations_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        self.local = parse_stations_toml(text)
        raw_saved = read_json(self.saved_path, [])
        self.saved = [
            Station.from_dict(item, source="radio-browser")
            for item in raw_saved
            if isinstance(item, dict) and item.get("url")
        ]

    # -- access ------------------------------------------------------------

    @property
    def favorite_urls(self) -> set[str]:
        return set(self.settings.get("favorites") or [])

    def all(self) -> list[Station]:
        seen: set[str] = set()
        combined: list[Station] = []
        favorites = self.favorite_urls
        for station in self.local + self.saved:
            if station.url in seen:
                continue
            seen.add(station.url)
            station.favorite = station.url in favorites
            combined.append(station)
        return combined

    def favorites(self) -> list[Station]:
        return [s for s in self.all() if s.favorite]

    def find(self, url: str) -> Station | None:
        for station in self.all():
            if station.url == url:
                return station
        return None

    # -- mutation ----------------------------------------------------------

    def toggle_favorite(self, station: Station) -> bool:
        favorites = self.favorite_urls
        if station.url in favorites:
            favorites.discard(station.url)
            station.favorite = False
        else:
            favorites.add(station.url)
            station.favorite = True
        self.settings.set("favorites", sorted(favorites))
        self.settings.save()
        return station.favorite

    def save_station(self, station: Station) -> bool:
        """Persist a searched station into saved.json.  False if already known."""
        if any(existing.url == station.url for existing in self.local + self.saved):
            return False
        self.saved.append(station)
        write_json(self.saved_path, [s.to_dict() for s in self.saved])
        return True

    def remove_saved(self, station: Station) -> bool:
        before = len(self.saved)
        self.saved = [s for s in self.saved if s.url != station.url]
        if len(self.saved) == before:
            return False
        write_json(self.saved_path, [s.to_dict() for s in self.saved])
        favorites = self.favorite_urls
        if station.url in favorites:
            favorites.discard(station.url)
            self.settings.set("favorites", sorted(favorites))
            self.settings.save()
        return True
