"""Unit tests for the pieces that do not need a display or a network."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cpzradio import config, recorder, runtime, scheduler  # noqa: E402
from cpzradio.library import Library, Station, parse_stations_toml  # noqa: E402


class TempHome(unittest.TestCase):
    """Redirects config/data dirs so tests never touch a real home."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved = {k: os.environ.get(k) for k in ("CPZRADIO_CONFIG_DIR", "CPZRADIO_DATA_DIR")}
        os.environ["CPZRADIO_CONFIG_DIR"] = str(root / "config")
        os.environ["CPZRADIO_DATA_DIR"] = str(root / "data")
        self.root = root

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()


class TestStationsToml(unittest.TestCase):
    def test_parses_entries(self):
        text = """
        [[station]]
        name = "One"
        url = "http://a/1"
        genre = "jazz"
        bitrate = 128

        [[station]]
        name = "Two"
        url = "http://a/2"
        """
        stations = parse_stations_toml(text)
        self.assertEqual([s.name for s in stations], ["One", "Two"])
        self.assertEqual(stations[0].bitrate, 128)
        self.assertEqual(stations[1].genre, "")

    def test_skips_entries_without_url(self):
        stations = parse_stations_toml('[[station]]\nname = "No URL"\n')
        self.assertEqual(stations, [])

    def test_malformed_toml_is_not_fatal(self):
        self.assertEqual(parse_stations_toml("[[station]\nbroken"), [])

    def test_shipped_station_list_is_valid(self):
        path = Path(__file__).resolve().parent.parent / "data" / "stations.toml"
        stations = parse_stations_toml(path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(stations), 5)
        for station in stations:
            self.assertTrue(station.url.startswith("http"), station.url)
            self.assertTrue(station.name)
        urls = [s.url for s in stations]
        self.assertEqual(len(urls), len(set(urls)), "duplicate stream URLs")

    def test_subtitle_formatting(self):
        station = Station(name="X", url="u", genre="jazz", country="US", bitrate=128)
        self.assertEqual(station.subtitle, "jazz · US · 128k")


class TestLibrary(TempHome):
    def _library(self) -> Library:
        settings = config.Settings()
        stations = Path(os.environ["CPZRADIO_CONFIG_DIR"]) / "stations.toml"
        stations.parent.mkdir(parents=True, exist_ok=True)
        stations.write_text(
            '[[station]]\nname = "Local"\nurl = "http://local/1"\n', encoding="utf-8"
        )
        return Library(settings, stations_path=stations)

    def test_favorites_round_trip(self):
        library = self._library()
        station = library.all()[0]
        self.assertFalse(station.favorite)
        self.assertTrue(library.toggle_favorite(station))
        self.assertEqual([s.name for s in library.favorites()], ["Local"])
        self.assertFalse(library.toggle_favorite(station))
        self.assertEqual(library.favorites(), [])

    def test_save_station_dedupes(self):
        library = self._library()
        found = Station(name="Found", url="http://found/1", source="radio-browser")
        self.assertTrue(library.save_station(found))
        self.assertFalse(library.save_station(found))
        library.reload()
        self.assertIn("Found", [s.name for s in library.all()])

    def test_remove_saved_clears_favorite(self):
        library = self._library()
        found = Station(name="Found", url="http://found/1", source="radio-browser")
        library.save_station(found)
        library.reload()
        target = library.find("http://found/1")
        library.toggle_favorite(target)
        self.assertTrue(library.remove_saved(target))
        self.assertNotIn("http://found/1", library.favorite_urls)

    def test_local_stations_cannot_be_removed(self):
        library = self._library()
        self.assertFalse(library.remove_saved(library.all()[0]))

    def test_seeding_copies_bundled_list(self):
        settings = config.Settings()
        target = Path(os.environ["CPZRADIO_CONFIG_DIR"]) / "stations.toml"
        library = Library(settings, stations_path=target)
        library.ensure_seeded(config.bundled_stations())
        library.reload()
        self.assertTrue(target.exists())
        self.assertGreater(len(library.all()), 0)


class TestSettings(TempHome):
    def test_defaults_and_persistence(self):
        settings = config.Settings()
        self.assertEqual(settings.get("volume"), 70)
        settings.set("volume", 42)
        settings.save()
        self.assertEqual(config.Settings().get("volume"), 42)

    def test_unknown_keys_are_dropped(self):
        settings = config.Settings()
        config.write_json(settings.path, {"volume": 10, "bogus": True})
        reloaded = config.Settings()
        self.assertEqual(reloaded.get("volume"), 10)
        self.assertIsNone(reloaded.get("bogus"))

    def test_partial_alarm_is_merged(self):
        settings = config.Settings()
        config.write_json(settings.path, {"alarm": {"hour": 9}})
        alarm = config.Settings().get("alarm")
        self.assertEqual(alarm["hour"], 9)
        self.assertIn("days", alarm)

    def test_corrupt_settings_file_falls_back(self):
        path = Path(os.environ["CPZRADIO_CONFIG_DIR"]) / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(config.Settings().get("volume"), 70)

    def test_atomic_write_leaves_no_temp_file(self):
        path = Path(os.environ["CPZRADIO_CONFIG_DIR"]) / "x.json"
        config.write_json(path, {"a": 1})
        self.assertEqual(json.loads(path.read_text()), {"a": 1})
        self.assertFalse(path.with_name(path.name + ".tmp").exists())


class TestSleepTimer(unittest.TestCase):
    def test_countdown_and_single_expiry(self):
        timer = scheduler.SleepTimer()
        self.assertFalse(timer.active)
        timer.start(30, now=1000.0)
        self.assertTrue(timer.active)
        self.assertEqual(timer.remaining(now=1000.0), 1800)
        self.assertFalse(timer.expired(now=2000.0))
        self.assertTrue(timer.expired(now=2800.0))
        # Only fires once.
        self.assertFalse(timer.expired(now=9999.0))
        self.assertFalse(timer.active)

    def test_label(self):
        timer = scheduler.SleepTimer()
        self.assertEqual(timer.label(), "off")
        timer.start(2, now=0.0)
        self.assertEqual(timer.label(now=45.0), "1:15")


class TestAlarm(unittest.TestCase):
    def test_next_occurrence_skips_non_selected_days(self):
        cfg = scheduler.AlarmConfig(enabled=True, hour=7, minute=0, days=(0,))  # Monday
        friday = datetime(2026, 8, 14, 9, 0)  # a Friday
        nxt = cfg.next_occurrence(friday)
        self.assertEqual(nxt.weekday(), 0)
        self.assertEqual((nxt.hour, nxt.minute), (7, 0))

    def test_disabled_alarm_has_no_occurrence(self):
        cfg = scheduler.AlarmConfig(enabled=False)
        self.assertIsNone(cfg.next_occurrence(datetime(2026, 8, 14, 9, 0)))

    def test_due_is_edge_triggered(self):
        cfg = scheduler.AlarmConfig(enabled=True, hour=7, minute=0, days=(4,))  # Friday
        alarm = scheduler.Alarm(cfg)
        moment = datetime(2026, 8, 14, 7, 0, 12)
        self.assertTrue(alarm.due(moment))
        self.assertFalse(alarm.due(moment))  # same minute, already fired
        self.assertFalse(alarm.due(datetime(2026, 8, 14, 7, 1)))
        self.assertTrue(alarm.due(datetime(2026, 8, 21, 7, 0)))  # next week

    def test_wrong_day_never_fires(self):
        cfg = scheduler.AlarmConfig(enabled=True, hour=7, minute=0, days=(0,))
        alarm = scheduler.Alarm(cfg)
        self.assertFalse(alarm.due(datetime(2026, 8, 14, 7, 0)))  # Friday

    def test_config_round_trip(self):
        cfg = scheduler.AlarmConfig.from_dict({"enabled": True, "hour": 25, "days": [9]})
        self.assertEqual(cfg.hour, 23)  # clamped
        self.assertEqual(cfg.days, (2,))  # 9 % 7
        self.assertEqual(scheduler.AlarmConfig.from_dict(cfg.to_dict()).days, (2,))

    def test_day_labels(self):
        self.assertEqual(scheduler.AlarmConfig(days=(0, 1, 2, 3, 4)).days_label, "weekdays")
        self.assertEqual(scheduler.AlarmConfig(days=(5, 6)).days_label, "weekends")
        self.assertEqual(
            scheduler.AlarmConfig(days=(0, 1, 2, 3, 4, 5, 6)).days_label, "daily"
        )


class TestRecorder(TempHome):
    def test_slugify_strips_unsafe_characters(self):
        self.assertEqual(recorder.slugify("SomaFM: Groove/Salad!"), "SomaFM-Groove-Salad")
        self.assertEqual(recorder.slugify("   "), "station")

    def test_extension_follows_codec(self):
        self.assertEqual(recorder.extension_for("MP3"), ".mp3")
        self.assertEqual(recorder.extension_for("aac"), ".aac")
        self.assertEqual(recorder.extension_for(""), ".mkv")
        self.assertEqual(recorder.extension_for("something-odd"), ".mkv")

    def test_build_path_is_timestamped(self):
        rec = recorder.Recorder(config.Settings())
        station = Station(name="Test FM", url="u", codec="MP3")
        path = rec.build_path(station, when=datetime(2026, 8, 13, 14, 5, 30))
        self.assertEqual(path.name, "2026-08-13_140530_Test-FM.mp3")

    def test_toggle_reports_when_nothing_plays(self):
        rec = recorder.Recorder(config.Settings())

        class FakePlayer:
            recording = False

        started, message = rec.toggle(FakePlayer(), None)
        self.assertFalse(started)
        self.assertIn("nothing", message)


class TestKeyMapping(unittest.TestCase):
    def test_evdev_names(self):
        self.assertEqual(runtime.key_name_from_evdev("KEY_A"), "a")
        self.assertEqual(runtime.key_name_from_evdev("KEY_7"), "7")
        self.assertEqual(runtime.key_name_from_evdev("KEY_ESC"), "escape")
        self.assertEqual(runtime.key_name_from_evdev("KEY_LEFTSHIFT"), "shift")
        self.assertEqual(runtime.key_name_from_evdev("KEY_SLASH"), "/")
        self.assertEqual(runtime.key_name_from_evdev("BTN_LEFT"), "")

    def test_characters_respect_shift(self):
        self.assertEqual(runtime.char_for("a", False), "a")
        self.assertEqual(runtime.char_for("a", True), "A")
        self.assertEqual(runtime.char_for("1", True), "!")
        self.assertEqual(runtime.char_for("space", False), " ")
        self.assertEqual(runtime.char_for("enter", False), "")


class TestPixelPacking(unittest.TestCase):
    def test_rgb565_endianness_and_length(self):
        from PIL import Image

        image = Image.new("RGB", (2, 1))
        image.putpixel((0, 0), (255, 0, 0))
        image.putpixel((1, 0), (0, 0, 255))
        packed = runtime.pack_rgb565(image)
        self.assertEqual(len(packed), 4)
        self.assertEqual(int.from_bytes(packed[0:2], "little"), 0xF800)  # red
        self.assertEqual(int.from_bytes(packed[2:4], "little"), 0x001F)  # blue

    def test_xrgb8888_is_bgrx(self):
        from PIL import Image

        image = Image.new("RGB", (1, 1), (10, 20, 30))
        self.assertEqual(runtime.pack_xrgb8888(image), bytes([30, 20, 10, 255]))

    def test_fallback_matches_numpy_path(self):
        from PIL import Image

        image = Image.new("RGB", (7, 3))
        for x in range(7):
            for y in range(3):
                image.putpixel((x, y), (x * 31 % 256, y * 77 % 256, (x + y) * 19 % 256))

        fast = runtime.pack_rgb565(image)
        saved = runtime._np
        try:
            runtime._np = None  # force the pure-Python path
            slow = runtime.pack_rgb565(image)
        finally:
            runtime._np = saved
        self.assertEqual(fast, slow)


if __name__ == "__main__":
    unittest.main()
