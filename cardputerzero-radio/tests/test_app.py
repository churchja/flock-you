"""Application-level key handling, driven with a stub player.

No mpv, no ALSA, no display - just the state machine.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cpzradio import mixer, player, radio  # noqa: E402
from cpzradio.runtime import KeyEvent  # noqa: E402


class StubPlayer:
    """Implements the surface RadioApp uses, and records what was asked of it."""

    def __init__(self):
        self.state = player.STOPPED
        self.url = None
        self.station_name = ""
        self.error_text = ""
        self.recording_path = None
        self.volume = 70
        self.played: list[str] = []
        self.stopped = 0
        self.polls = 0

    def play(self, url, name=""):
        self.url = url
        self.station_name = name
        self.state = player.PLAYING
        self.played.append(url)

    def stop(self):
        self.url = None
        self.state = player.STOPPED
        self.stopped += 1

    def poll(self, now=None):
        self.polls += 1

    def set_volume(self, value):
        self.volume = max(0, min(100, int(value)))
        return self.volume

    def start_recording(self, path):
        self.recording_path = path
        return True

    def stop_recording(self):
        self.recording_path = None

    @property
    def recording(self):
        return self.recording_path is not None

    @property
    def now_playing(self):
        return ""

    @property
    def buffered_seconds(self):
        return 0.0

    def shutdown(self):
        pass


def press(app, name, char=""):
    app.on_key(KeyEvent(name, char, True, False))


class AppTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved = {
            k: os.environ.get(k) for k in ("CPZRADIO_CONFIG_DIR", "CPZRADIO_DATA_DIR")
        }
        os.environ["CPZRADIO_CONFIG_DIR"] = str(root / "config")
        os.environ["CPZRADIO_DATA_DIR"] = str(root / "data")

        # No real mixer: keeps volume in software and avoids forking amixer.
        patcher = mock.patch.object(mixer.AlsaMixer, "available", staticmethod(lambda: False))
        patcher.start()
        self.addCleanup(patcher.stop)

        self.player = StubPlayer()
        self.app = radio.RadioApp(player=self.player, autoplay=False)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()


class TestVolumeKeys(AppTest):
    def test_arrows_change_volume_on_now_playing(self):
        start = self.app.volume.level
        press(self.app, "right")
        self.assertEqual(self.app.volume.level, start + 5)
        press(self.app, "left")
        press(self.app, "left")
        self.assertEqual(self.app.volume.level, start - 5)

    def test_volume_clamps(self):
        self.app.volume.set(98)
        press(self.app, "right")
        self.assertEqual(self.app.volume.level, 100)
        self.app.volume.set(2)
        press(self.app, "left")
        self.assertEqual(self.app.volume.level, 0)

    def test_mute_restores_previous_level(self):
        self.app.volume.set(55)
        press(self.app, "m")
        self.assertEqual(self.app.volume.level, 0)
        press(self.app, "m")
        self.assertEqual(self.app.volume.level, 55)


class TestSettingsKeys(AppTest):
    """Regression: arrows must edit the selected row, not the volume."""

    def test_arrows_edit_the_selected_row_not_volume(self):
        self.app.open_settings()
        self.app.settings_index = 3  # Alarm time
        before_volume = self.app.volume.level
        press(self.app, "right")
        self.assertEqual(self.app.alarm.config.clock, "07:05")
        self.assertEqual(self.app.volume.level, before_volume)
        press(self.app, "left")
        press(self.app, "left")
        self.assertEqual(self.app.alarm.config.clock, "06:55")

    def test_alarm_time_wraps_at_midnight(self):
        self.app.open_settings()
        self.app.settings_index = 3
        self.app.alarm.config.hour, self.app.alarm.config.minute = 0, 0
        press(self.app, "left")
        self.assertEqual(self.app.alarm.config.clock, "23:55")

    def test_volume_row_still_uses_arrows(self):
        self.app.open_settings()
        self.app.settings_index = 0
        start = self.app.volume.level
        press(self.app, "right")
        self.assertEqual(self.app.volume.level, start + 5)

    def test_alarm_days_cycle(self):
        self.app.open_settings()
        self.app.settings_index = 4
        self.assertEqual(self.app.alarm.config.days_label, "weekdays")
        press(self.app, "right")
        self.assertEqual(self.app.alarm.config.days_label, "weekends")
        press(self.app, "right")
        self.assertEqual(self.app.alarm.config.days_label, "daily")

    def test_enter_toggles_alarm_and_persists(self):
        self.app.open_settings()
        self.app.settings_index = 2
        press(self.app, "enter")
        self.assertTrue(self.app.alarm.config.enabled)
        self.assertTrue(self.app.settings.get("alarm")["enabled"])

    def test_enter_starts_and_cancels_sleep_timer(self):
        self.app.open_settings()
        self.app.settings_index = 1
        press(self.app, "enter")
        self.assertTrue(self.app.sleep.active)
        press(self.app, "enter")
        self.assertFalse(self.app.sleep.active)

    def test_autostart_status_is_not_polled_per_frame(self):
        """Regression: settings_rows() ran systemctl on every rendered frame."""
        with mock.patch.object(radio.autostart, "status_label", return_value="off") as probe:
            self.app.open_settings()
            for _ in range(50):
                self.app.settings_rows()
        self.assertEqual(probe.call_count, 1)


class TestNavigation(AppTest):
    def test_tab_cycles_screens(self):
        with mock.patch.object(radio.autostart, "status_label", return_value="off"):
            self.assertEqual(self.app.screen, "now")
            press(self.app, "tab")
            self.assertEqual(self.app.screen, "stations")
            press(self.app, "tab")
            self.assertEqual(self.app.screen, "settings")
            press(self.app, "tab")
            self.assertEqual(self.app.screen, "now")

    def test_escape_backs_out_then_quits(self):
        press(self.app, "s")
        self.assertEqual(self.app.screen, "stations")
        press(self.app, "escape")
        self.assertEqual(self.app.screen, "now")
        self.assertTrue(self.app.running)
        press(self.app, "escape")
        self.assertFalse(self.app.running)

    def test_q_quits(self):
        press(self.app, "q")
        self.assertFalse(self.app.running)


class TestTuning(AppTest):
    def test_up_down_tunes_and_wraps(self):
        total = len(self.app.visible_stations)
        self.assertGreater(total, 1)
        press(self.app, "down")
        self.assertEqual(self.app.station_index, 1)
        self.assertEqual(len(self.player.played), 1)
        press(self.app, "up")
        press(self.app, "up")
        self.assertEqual(self.app.station_index, total - 1)

    def test_space_toggles_playback(self):
        press(self.app, "space")
        self.assertEqual(self.player.state, player.PLAYING)
        press(self.app, "space")
        self.assertEqual(self.player.state, player.STOPPED)
        self.assertEqual(self.player.stopped, 1)

    def test_playing_persists_last_url(self):
        press(self.app, "space")
        self.assertTrue(self.app.settings.get("last_url"))

    def test_favourite_toggle_and_filter(self):
        press(self.app, "s")
        press(self.app, "f")
        self.assertEqual(len(self.app.library.favorites()), 1)
        press(self.app, "t")
        self.assertTrue(self.app.favorites_only)
        self.assertEqual(len(self.app.visible_stations), 1)

    def test_removing_a_local_station_is_refused(self):
        press(self.app, "s")
        count = len(self.app.visible_stations)
        press(self.app, "x")
        self.assertEqual(len(self.app.visible_stations), count)
        self.assertIn("searched", self.app.notice)


class TestSearchScreen(AppTest):
    def test_typing_builds_a_query(self):
        press(self.app, "/")
        self.assertEqual(self.app.screen, "search")
        for name in "jazz":
            press(self.app, name, name)
        press(self.app, "space", " ")
        press(self.app, "1", "1")
        self.assertEqual(self.app.query, "jazz 1")
        press(self.app, "backspace")
        self.assertEqual(self.app.query, "jazz ")

    def test_letters_do_not_trigger_global_shortcuts(self):
        """q/g/m/r must type, not quit or open menus, while searching."""
        press(self.app, "/")
        for name in "qgmr":
            press(self.app, name, name)
        self.assertEqual(self.app.query, "qgmr")
        self.assertTrue(self.app.running)
        self.assertEqual(self.app.screen, "search")

    def test_escape_returns_to_now_playing(self):
        press(self.app, "/")
        press(self.app, "escape")
        self.assertEqual(self.app.screen, "now")

    def test_empty_query_search_is_rejected(self):
        press(self.app, "/")
        press(self.app, "enter")
        self.assertIsNone(self.app.search_job)
        self.assertIn("search", self.app.notice)


class TestUpdateLoop(AppTest):
    def test_sleep_expiry_stops_playback(self):
        press(self.app, "space")
        self.app.sleep.start(1, now=0.0)
        self.app.update(now=61.0)
        self.assertEqual(self.player.stopped, 1)
        self.assertIn("sleep", self.app.notice)

    def test_alarm_fires_and_tunes(self):
        from datetime import datetime

        target = self.app.visible_stations[2]
        self.app.alarm.config.enabled = True
        self.app.alarm.config.url = target.url
        moment = datetime(2026, 8, 14, 7, 0)
        self.app.alarm.config.hour, self.app.alarm.config.minute = 7, 0
        self.app.alarm.config.days = (moment.weekday(),)
        with mock.patch("cpzradio.radio.datetime") as fake:
            fake.now.return_value = moment
            self.app.update()
        self.assertEqual(self.player.url, target.url)
        self.assertEqual(self.app.screen, "now")

    def test_update_polls_the_player(self):
        self.app.update()
        self.assertEqual(self.player.polls, 1)


if __name__ == "__main__":
    unittest.main()
