"""Player tests.

The IPC layer is exercised against a stub server that speaks mpv's wire
protocol, so the JSON framing, request/response correlation and event handling
are all verified without mpv installed.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cpzradio import library, player  # noqa: E402


class StubMpv:
    """A minimal server that answers like mpv's --input-ipc-server socket."""

    def __init__(self, path: str):
        self.path = path
        self.received: list[dict] = []
        self.observed: dict[int, str] = {}
        self.properties: dict[str, object] = {"volume": 70}
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(path)
        self._server.listen(1)
        self._conn: socket.socket | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self._server.accept()
        except OSError:
            return
        self._conn = conn
        buffer = b""
        while not self._stop.is_set():
            try:
                chunk = conn.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line.strip():
                    self._handle(json.loads(line.decode()))

    def _handle(self, message: dict) -> None:
        self.received.append(message)
        command = message.get("command") or []
        data = None
        if command and command[0] == "get_property":
            data = self.properties.get(command[1])
        elif command and command[0] == "set_property":
            self.properties[command[1]] = command[2]
        elif command and command[0] == "observe_property":
            self.observed[command[1]] = command[2]
        if "request_id" in message:
            self.send({"request_id": message["request_id"], "error": "success", "data": data})

    def send(self, payload: dict) -> None:
        if self._conn is None:
            return
        try:
            self._conn.sendall(json.dumps(payload).encode() + b"\n")
        except OSError:
            pass

    def wait_for_connection(self, timeout: float = 5.0) -> None:
        import time

        deadline = time.monotonic() + timeout
        while self._conn is None and time.monotonic() < deadline:
            time.sleep(0.01)

    def close(self) -> None:
        self._stop.set()
        for sock in (self._conn, self._server):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        try:
            os.unlink(self.path)
        except OSError:
            pass


class TestMpvIPC(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sock_path = os.path.join(self._tmp.name, "mpv.sock")
        self.server = StubMpv(self.sock_path)
        self.events: list[dict] = []
        self.ipc = player.MpvIPC(self.sock_path, on_event=self.events.append)
        self.ipc.connect(timeout=5)
        self.server.wait_for_connection()

    def tearDown(self):
        self.ipc.close()
        self.server.close()
        self._tmp.cleanup()

    def test_command_round_trip(self):
        self.assertEqual(self.ipc.get_property("volume"), 70)

    def test_set_property_reaches_server(self):
        self.ipc.set_property("volume", 33, wait=True)
        self.assertEqual(self.server.properties["volume"], 33)

    def test_request_ids_are_unique_and_correlated(self):
        self.ipc.set_property("a", 1, wait=True)
        self.ipc.set_property("b", 2, wait=True)
        ids = [m["request_id"] for m in self.server.received if "request_id" in m]
        self.assertEqual(len(ids), len(set(ids)))

    def test_observe_property_registers(self):
        self.ipc.observe_property(9, "metadata")
        self.ipc.get_property("volume")  # round trip to flush the pipeline
        self.assertEqual(self.server.observed.get(9), "metadata")

    def test_events_are_dispatched(self):
        self.server.send({"event": "file-loaded"})
        self.ipc.get_property("volume")
        self.assertIn("file-loaded", [e.get("event") for e in self.events])

    def test_error_response_returns_none(self):
        # A command the stub answers with an mpv-style error.
        request_id = 999
        self.ipc._pending[request_id] = {"request_id": request_id, "error": "property not found"}
        self.assertIsNone(self.ipc.command("get_property", "nope", timeout=0.2))

    def test_command_without_connection_raises(self):
        self.ipc.close()
        with self.assertRaises(ConnectionError):
            self.ipc.command("get_property", "volume")


class TestPlayerStateMachine(unittest.TestCase):
    """Drives MpvPlayer's event handler directly - no process, no socket."""

    def setUp(self):
        self.player = player.MpvPlayer()
        self.player.url = "http://stream/1"
        self.player.station_name = "Test"

    def test_file_loaded_marks_playing(self):
        self.player.state = player.CONNECTING
        self.player._on_event({"event": "file-loaded"})
        self.assertEqual(self.player.state, player.PLAYING)

    def test_end_file_error_schedules_retry_with_backoff(self):
        self.player._on_event({"event": "end-file", "reason": "error"})
        self.assertEqual(self.player.state, player.ERROR)
        first_delay = self.player._retry_at
        self.assertGreater(first_delay, 0)
        self.assertEqual(self.player._retry_step, 1)

        self.player._on_event({"event": "end-file", "reason": "error"})
        self.assertEqual(self.player._retry_step, 2)

    def test_quit_reason_is_ignored(self):
        self.player.state = player.PLAYING
        self.player._on_event({"event": "end-file", "reason": "quit"})
        self.assertEqual(self.player.state, player.PLAYING)

    def test_success_resets_backoff(self):
        self.player._on_event({"event": "end-file", "reason": "error"})
        self.player._on_event({"event": "file-loaded"})
        self.assertEqual(self.player._retry_step, 0)
        self.assertEqual(self.player.error_text, "")

    def test_events_ignored_when_stopped(self):
        self.player.url = None
        self.player._on_event({"event": "end-file", "reason": "error"})
        self.assertNotEqual(self.player.state, player.ERROR)

    def test_property_changes_are_mirrored(self):
        self.player._prop_names[1] = "metadata"
        self.player._on_event(
            {"event": "property-change", "id": 1, "data": {"icy-title": "Song - Artist"}}
        )
        self.assertEqual(self.player.now_playing, "Song - Artist")

    def test_now_playing_prefers_icy_title(self):
        self.player._props["metadata"] = {"icy-title": "  Real Title  "}
        self.player._props["media-title"] = "fallback"
        self.assertEqual(self.player.now_playing, "Real Title")

    def test_now_playing_ignores_url_echo(self):
        self.player._props["media-title"] = self.player.url
        self.assertEqual(self.player.now_playing, "")

    def test_now_playing_empty_without_metadata(self):
        self.assertEqual(self.player.now_playing, "")

    def test_buffered_seconds_defaults_to_zero(self):
        self.assertEqual(self.player.buffered_seconds, 0.0)
        self.player._props["demuxer-cache-time"] = 12.5
        self.assertEqual(self.player.buffered_seconds, 12.5)

    def test_poll_retries_after_backoff_elapses(self):
        self.player._on_event({"event": "end-file", "reason": "error"})
        sent = []
        self.player.ipc = mock.Mock()
        self.player.ipc.command.side_effect = lambda *a, **k: sent.append(a)
        self.player.poll(now=self.player._retry_at + 1)
        self.assertEqual(self.player.state, player.CONNECTING)
        self.assertEqual(sent[0][0], "loadfile")

    def test_poll_does_nothing_before_backoff(self):
        self.player._on_event({"event": "end-file", "reason": "error"})
        self.player.ipc = mock.Mock()
        self.player.poll(now=0)
        self.player.ipc.command.assert_not_called()
        self.assertEqual(self.player.state, player.ERROR)

    def test_missing_mpv_raises_clearly(self):
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(player.MpvNotInstalled):
                player.MpvPlayer().start()

    def test_argv_includes_reconnect_and_ipc(self):
        argv = self.player._argv("/tmp/x.sock")
        joined = " ".join(argv)
        self.assertIn("--input-ipc-server=/tmp/x.sock", argv)
        self.assertIn("--idle=yes", argv)
        self.assertIn("--no-video", argv)
        self.assertIn("reconnect=1", joined)


class TestStreamUrlResolution(unittest.TestCase):
    def _fake_response(self, body: str):
        class FakeResponse:
            def read(self, _n=None):
                return body.encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return FakeResponse()

    def test_plain_url_untouched(self):
        self.assertEqual(
            library.resolve_stream_url("http://host/stream.mp3"), "http://host/stream.mp3"
        )

    def test_hls_untouched(self):
        # mpv handles .m3u8 natively; unwrapping it would break adaptive streams.
        url = "http://host/live.m3u8"
        self.assertEqual(library.resolve_stream_url(url), url)

    def test_pls_is_unwrapped(self):
        body = "[playlist]\nNumberOfEntries=1\nFile1=http://host/real.mp3\nTitle1=x\n"
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(body)):
            self.assertEqual(
                library.resolve_stream_url("http://host/x.pls"), "http://host/real.mp3"
            )

    def test_m3u_is_unwrapped(self):
        body = "#EXTM3U\n#EXTINF:-1,Station\nhttp://host/real.aac\n"
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(body)):
            self.assertEqual(
                library.resolve_stream_url("http://host/x.m3u"), "http://host/real.aac"
            )

    def test_unreachable_playlist_falls_back_to_original(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("boom")):
            self.assertEqual(
                library.resolve_stream_url("http://host/x.pls"), "http://host/x.pls"
            )


class TestRadioBrowserParsing(unittest.TestCase):
    def test_station_mapping_prefers_resolved_url(self):
        station = library.RadioBrowser._station(
            {
                "name": " Jazz FM ",
                "url": "http://old/",
                "url_resolved": "http://new/",
                "tags": "jazz,smooth",
                "countrycode": "GB",
                "codec": "MP3",
                "bitrate": "128",
                "stationuuid": "abc",
            }
        )
        self.assertEqual(station.url, "http://new/")
        self.assertEqual(station.name, "Jazz FM")
        self.assertEqual(station.genre, "jazz")
        self.assertEqual(station.bitrate, 128)
        self.assertEqual(station.source, "radio-browser")

    def test_missing_fields_are_tolerated(self):
        station = library.RadioBrowser._station({})
        self.assertEqual(station.name, "Unnamed")
        self.assertEqual(station.bitrate, 0)


if __name__ == "__main__":
    unittest.main()
