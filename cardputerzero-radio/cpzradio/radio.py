"""The application: state, key handling and per-frame updates."""

from __future__ import annotations

import time
from datetime import datetime

from . import autostart, config, ui
from .library import Library, RadioBrowser, SearchJob, Station, resolve_stream_url
from .mixer import VolumeControl
from .player import CONNECTING, PLAYING, MpvNotInstalled, MpvPlayer
from .recorder import Recorder
from .scheduler import SLEEP_PRESETS, Alarm, AlarmConfig, SleepTimer

DAY_PRESETS = (
    ((0, 1, 2, 3, 4), "weekdays"),
    ((5, 6), "weekends"),
    ((0, 1, 2, 3, 4, 5, 6), "daily"),
)


class RadioApp:
    def __init__(self, settings=None, library=None, player=None, autoplay=True):
        self.settings = settings or config.Settings()
        self.library = library or Library(self.settings)
        self.library.ensure_seeded(config.bundled_stations())
        self.library.reload()

        self.player = player or MpvPlayer(volume=int(self.settings.get("volume", 70)))
        self.volume = VolumeControl(self.player, self.settings)
        self.recorder = Recorder(self.settings)
        self.sleep = SleepTimer()
        self.alarm = Alarm(AlarmConfig.from_dict(self.settings.get("alarm", {})))
        self.browser = RadioBrowser()

        self.running = True
        self.screen = "now"
        self.phase = 0.0
        self._started = time.monotonic()
        self.notice = ""
        self._notice_until = 0.0

        self.station_index = 0
        self.favorites_only = False
        self.settings_index = 0

        self.query = ""
        self.search_results: list[Station] = []
        self.search_index = 0
        self.search_focus = "query"
        self.search_job: SearchJob | None = None
        self._searched_query = ""

        self._image = None
        self._muted_level: int | None = None
        # settings_rows() runs every frame; querying systemd there would fork
        # `systemctl` 20 times a second, so the status is cached instead.
        self._autostart_status = "?"

        self._restore_last(autoplay)

    # -- helpers -----------------------------------------------------------

    def _restore_last(self, autoplay: bool) -> None:
        last_url = self.settings.get("last_url") or ""
        stations = self.visible_stations
        for index, station in enumerate(stations):
            if station.url == last_url:
                self.station_index = index
                if autoplay:
                    self.tune(station, announce=False)
                return
        if stations:
            self.station_index = 0

    def set_notice(self, text: str, seconds: float = 2.5) -> None:
        self.notice = text
        self._notice_until = time.monotonic() + seconds

    @property
    def visible_stations(self) -> list[Station]:
        stations = self.library.all()
        if self.favorites_only:
            stations = [s for s in stations if s.favorite]
        return stations

    @property
    def selected_station(self) -> Station | None:
        stations = self.visible_stations
        if not stations:
            return None
        self.station_index = max(0, min(self.station_index, len(stations) - 1))
        return stations[self.station_index]

    @property
    def current_station(self) -> Station | None:
        if self.player.url:
            found = self.library.find(self.player.url)
            if found:
                return found
            return Station(name=self.player.station_name or "Stream", url=self.player.url)
        return self.selected_station

    def tune(self, station: Station | None, announce: bool = True) -> None:
        if station is None:
            return
        url = resolve_stream_url(station.url)
        try:
            self.player.play(url, station.name)
        except MpvNotInstalled as exc:
            self.set_notice(str(exc), 6)
            return
        except (ConnectionError, OSError) as exc:
            self.set_notice(f"player error: {exc}", 5)
            return
        self.settings.set("last_url", station.url)
        self.settings.save()
        if announce:
            self.set_notice(station.name, 1.5)

    def step_station(self, delta: int) -> None:
        stations = self.visible_stations
        if not stations:
            return
        self.station_index = (self.station_index + delta) % len(stations)
        self.tune(stations[self.station_index])

    def toggle_play(self) -> None:
        if self.player.state in (PLAYING, CONNECTING):
            self.player.stop()
            self.set_notice("stopped", 1.2)
        else:
            self.tune(self.current_station)

    def toggle_mute(self) -> None:
        if self._muted_level is None:
            self._muted_level = self.volume.level
            self.volume.set(0)
            self.set_notice("muted", 1.2)
        else:
            self.volume.set(self._muted_level)
            self._muted_level = None
            self.set_notice("unmuted", 1.2)

    def toggle_record(self) -> None:
        started, message = self.recorder.toggle(self.player, self.current_station)
        self.set_notice(message, 3 if not started else 2)

    def toggle_favorite(self, station: Station | None) -> None:
        if station is None:
            return
        now_fav = self.library.toggle_favorite(station)
        self.set_notice(("added to" if now_fav else "removed from") + " favourites", 1.5)

    # -- search ------------------------------------------------------------

    def start_search(self) -> None:
        query = self.query.strip()
        if not query:
            self.set_notice("type something to search", 2)
            return
        if self.search_job and not self.search_job.finished:
            return
        self._searched_query = query
        self.search_results = []
        self.search_index = 0
        self.search_job = SearchJob(self.browser, query)

    def search_status(self) -> str:
        if self.search_job and not self.search_job.finished:
            return "searching…"
        if self.search_job and self.search_job.error:
            return f"failed: {self.search_job.error[:44]}"
        if self.search_results:
            return f"{len(self.search_results)} results for “{self._searched_query}”"
        if self.search_job and self.search_job.finished:
            return "no matches"
        return ""

    def _collect_search(self) -> None:
        job = self.search_job
        if job is None or not job.finished:
            return
        if job.results:
            self.search_results = job.results
            self.search_index = 0
            self.search_focus = "results"
        self.search_job = None

    # -- settings rows -----------------------------------------------------

    def open_settings(self) -> None:
        """Enter Settings, refreshing the values that are expensive to poll."""
        self._autostart_status = autostart.status_label()
        self.screen = "settings"

    def settings_rows(self) -> list[tuple[str, str, str]]:
        alarm = self.alarm.config
        alarm_station = self.library.find(alarm.url)
        return [
            ("Volume", f"{self.volume.level}%", "left/right to adjust"),
            (
                "Sleep timer",
                self.sleep.label() if self.sleep.active else f"{self.settings.get('sleep_minutes', 30)}m (off)",
                "ENTER starts or cancels the countdown",
            ),
            ("Alarm", "on" if alarm.enabled else "off", "ENTER toggles wake-to-radio"),
            ("Alarm time", alarm.clock, "left/right by 5 minutes"),
            ("Alarm days", alarm.days_label, "left/right cycles presets"),
            (
                "Alarm station",
                alarm_station.name if alarm_station else "last played",
                "left/right picks the station to wake to",
            ),
            ("Autostart", self._autostart_status, "ENTER toggles boot-to-radio"),
            ("Recordings", str(self.recorder.directory), "set recordings_dir in settings.json"),
            ("Audio out", self.volume.backend, "detected volume backend"),
            ("About", f"v{config.VERSION}", "cardputerzero-radio"),
        ]

    def _adjust_setting(self, delta: int) -> None:
        index = self.settings_index
        alarm = self.alarm.config
        if index == 0:
            self.volume.nudge(delta * self.volume.STEP)
            self.settings.save()
        elif index == 1:
            presets = list(SLEEP_PRESETS)
            current = int(self.settings.get("sleep_minutes", 30))
            position = presets.index(current) if current in presets else 1
            value = presets[(position + delta) % len(presets)]
            self.settings.set("sleep_minutes", value)
            self.settings.save()
        elif index == 3:
            total = (alarm.hour * 60 + alarm.minute + delta * 5) % (24 * 60)
            alarm.hour, alarm.minute = divmod(total, 60)
            self._save_alarm()
        elif index == 4:
            presets = [days for days, _ in DAY_PRESETS]
            position = presets.index(alarm.days) if alarm.days in presets else 0
            alarm.days = presets[(position + delta) % len(presets)]
            self._save_alarm()
        elif index == 5:
            stations = self.library.all()
            if not stations:
                return
            urls = [""] + [s.url for s in stations]
            position = urls.index(alarm.url) if alarm.url in urls else 0
            alarm.url = urls[(position + delta) % len(urls)]
            self._save_alarm()

    def _activate_setting(self) -> None:
        index = self.settings_index
        if index == 1:
            if self.sleep.active:
                self.sleep.cancel()
                self.set_notice("sleep timer cancelled")
            else:
                minutes = int(self.settings.get("sleep_minutes", 30))
                self.sleep.start(minutes)
                self.set_notice(f"sleeping in {minutes}m")
        elif index == 2:
            self.alarm.config.enabled = not self.alarm.config.enabled
            self._save_alarm()
            self.set_notice("alarm " + ("on" if self.alarm.config.enabled else "off"))
        elif index == 6:
            ok, message = autostart.set_enabled(not autostart.is_enabled())
            self._autostart_status = autostart.status_label()
            self.set_notice(message, 3 if not ok else 2)

    def _save_alarm(self) -> None:
        self.settings.set("alarm", self.alarm.config.to_dict())
        self.settings.save()

    # -- key handling ------------------------------------------------------

    def on_key(self, event) -> None:
        if not event.pressed:
            return
        if self.screen == "search":
            self._keys_search(event)
            return

        name = event.name
        if name in ("left", "right"):
            # On Settings the arrows edit the selected row; everywhere else
            # they are the volume knob.
            if self.screen == "settings":
                self._adjust_setting(1 if name == "right" else -1)
            else:
                self.volume.nudge(self.volume.STEP if name == "right" else -self.volume.STEP)
                self.settings.save()
            return
        if name == "escape":
            if self.screen == "now":
                self.running = False
            else:
                self.screen = "now"
            return
        if name == "q":
            self.running = False
            return
        if name == "space":
            self.toggle_play()
            return
        if name == "r":
            self.toggle_record()
            return
        if name == "m":
            self.toggle_mute()
            return
        if name == "/":
            self.screen = "search"
            self.search_focus = "query"
            return
        if name == "g":
            self.open_settings()
            return
        if name == "tab":
            order = ["now", "stations", "settings"]
            nxt = order[(order.index(self.screen) + 1) % len(order)] if self.screen in order else "now"
            if nxt == "settings":
                self.open_settings()
            else:
                self.screen = nxt
            return

        if self.screen == "now":
            self._keys_now(event)
        elif self.screen == "stations":
            self._keys_stations(event)
        elif self.screen == "settings":
            self._keys_settings(event)

    def _keys_now(self, event) -> None:
        name = event.name
        if name == "up":
            self.step_station(-1)
        elif name == "down":
            self.step_station(1)
        elif name in ("enter", "s"):
            self.screen = "stations"
        elif name == "f":
            self.toggle_favorite(self.current_station)

    def _keys_stations(self, event) -> None:
        name = event.name
        stations = self.visible_stations
        if name == "up" and stations:
            self.station_index = (self.station_index - 1) % len(stations)
        elif name == "down" and stations:
            self.station_index = (self.station_index + 1) % len(stations)
        elif name == "enter":
            self.tune(self.selected_station)
            self.screen = "now"
        elif name == "f":
            self.toggle_favorite(self.selected_station)
        elif name == "t":
            self.favorites_only = not self.favorites_only
            self.station_index = 0
            self.set_notice("favourites only" if self.favorites_only else "all stations", 1.5)
        elif name == "x":
            station = self.selected_station
            if station and self.library.remove_saved(station):
                self.set_notice(f"removed {station.name}", 2)
                self.station_index = max(0, self.station_index - 1)
            elif station:
                self.set_notice("only searched stations can be removed", 3)
        elif name == "s":
            self.screen = "now"

    def _keys_settings(self, event) -> None:
        name = event.name
        count = len(self.settings_rows())
        if name == "up":
            self.settings_index = (self.settings_index - 1) % count
        elif name == "down":
            self.settings_index = (self.settings_index + 1) % count
        elif name == "enter":
            self._activate_setting()
        elif name == "s":
            self.screen = "stations"

    def _keys_search(self, event) -> None:
        name = event.name
        if name == "escape":
            if self.search_focus == "results" and self.search_results:
                self.search_focus = "query"
            else:
                self.screen = "now"
            return
        if name == "enter":
            if self.search_focus == "results" and self.search_results:
                station = self.search_results[self.search_index]
                self.library.save_station(station)
                self.library.reload()
                self.tune(station)
                self.screen = "now"
            else:
                self.start_search()
            return
        if name == "up":
            if self.search_results:
                self.search_focus = "results"
                self.search_index = (self.search_index - 1) % len(self.search_results)
            return
        if name == "down":
            if self.search_results:
                self.search_focus = "results"
                self.search_index = (self.search_index + 1) % len(self.search_results)
            return
        if name == "tab":
            if self.search_results:
                station = self.search_results[self.search_index]
                added = self.library.save_station(station)
                self.library.reload()
                self.set_notice("saved" if added else "already in library", 1.5)
            return
        if name == "backspace":
            self.query = self.query[:-1]
            self.search_focus = "query"
            return
        if event.char and event.char.isprintable():
            self.query += event.char
            self.search_focus = "query"

    # -- loop hooks --------------------------------------------------------

    def update(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.phase = now - self._started

        if self.notice and now >= self._notice_until:
            self.notice = ""

        self.player.poll(now)
        self._collect_search()

        if self.sleep.expired(now):
            self.player.stop()
            self.set_notice("sleep timer - stopped", 4)

        if self.alarm.due(datetime.now()):
            target = self.library.find(self.alarm.config.url) or self.current_station
            self.screen = "now"
            self.tune(target)
            self.set_notice("alarm", 5)

    def draw(self):
        from PIL import Image

        if self._image is None:
            self._image = Image.new("RGB", (ui.theme.WIDTH, ui.theme.HEIGHT), ui.theme.BG)
        image = ui.render(self._image, self)
        if self.notice:
            painter = ui.Painter(image)
            painter.footer("", self.notice)
        return image

    def shutdown(self) -> None:
        try:
            self.settings.set("volume", self.volume.level)
            self.settings.save()
        except OSError:
            pass
        self.player.shutdown()
