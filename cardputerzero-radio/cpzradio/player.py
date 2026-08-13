"""Stream playback driven by an ``mpv`` child process over its JSON IPC socket.

mpv is used in ``--idle`` mode: it starts once and stays resident, so switching
stations is a single ``loadfile`` rather than a process respawn.  The IPC socket
also gives us ICY ``now playing`` titles and stream recording for free.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time

# Playback states surfaced to the UI.
STOPPED = "stopped"
CONNECTING = "connecting"
PLAYING = "playing"
ERROR = "error"

# Backoff schedule (seconds) applied when a stream drops.
RETRY_BACKOFF = (2, 4, 8, 15, 30)


class MpvNotInstalled(RuntimeError):
    """Raised when the mpv binary is missing."""


class MpvIPC:
    """Newline-delimited JSON client for mpv's ``--input-ipc-server`` socket.

    Kept separate from process management so the protocol can be tested
    against a stub server.
    """

    def __init__(self, path: str, on_event=None):
        self.path = path
        self.on_event = on_event
        self.sock: socket.socket | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._ready = threading.Condition()
        self._reader: threading.Thread | None = None
        self._closing = False

    def connect(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(self.path)
            except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
                last_error = exc
                time.sleep(0.1)
                continue
            sock.settimeout(None)
            self.sock = sock
            self._closing = False
            self._reader = threading.Thread(
                target=self._read_loop, name="mpv-ipc", daemon=True
            )
            self._reader.start()
            return
        raise ConnectionError(f"could not connect to mpv IPC at {self.path}: {last_error}")

    def _read_loop(self) -> None:
        buffer = b""
        sock = self.sock
        while sock is not None and not self._closing:
            try:
                chunk = sock.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    message = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                self._dispatch(message)
        with self._ready:
            self._ready.notify_all()

    def _dispatch(self, message: dict) -> None:
        if "request_id" in message:
            with self._ready:
                self._pending[message["request_id"]] = message
                self._ready.notify_all()
            return
        if "event" in message and self.on_event:
            try:
                self.on_event(message)
            except Exception:  # noqa: BLE001 - a bad handler must not kill the reader
                pass

    def command(self, *args, timeout: float = 3.0, wait: bool = True):
        """Send a command.  Returns mpv's ``data`` field, or None."""
        if self.sock is None:
            raise ConnectionError("not connected to mpv")
        request_id = next(self._ids)
        payload = json.dumps({"command": list(args), "request_id": request_id})
        with self._lock:
            try:
                self.sock.sendall(payload.encode("utf-8") + b"\n")
            except OSError as exc:
                raise ConnectionError(f"mpv IPC write failed: {exc}") from exc
        if not wait:
            return None

        deadline = time.monotonic() + timeout
        with self._ready:
            while request_id not in self._pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._ready.wait(remaining)
            message = self._pending.pop(request_id)
        if message.get("error") not in (None, "success"):
            return None
        return message.get("data")

    def set_property(self, name: str, value, wait: bool = False):
        return self.command("set_property", name, value, wait=wait)

    def get_property(self, name: str, timeout: float = 3.0):
        return self.command("get_property", name, timeout=timeout)

    def observe_property(self, obs_id: int, name: str):
        return self.command("observe_property", obs_id, name, wait=False)

    def close(self) -> None:
        self._closing = True
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


class MpvPlayer:
    """Owns the mpv process and exposes a small radio-shaped API."""

    # Properties we keep mirrored locally via observe_property.
    _OBSERVED = ("metadata", "media-title", "core-idle", "pause", "volume", "demuxer-cache-time")

    def __init__(self, volume: int = 70, audio_device: str | None = None, extra_args=()):
        self.proc: subprocess.Popen | None = None
        self.ipc: MpvIPC | None = None
        self.state = STOPPED
        self.url: str | None = None
        self.station_name: str = ""
        self.error_text: str = ""
        self.recording_path: str | None = None

        self._volume = max(0, min(100, int(volume)))
        self._audio_device = audio_device
        self._extra_args = list(extra_args)
        self._props: dict[str, object] = {}
        self._prop_names: dict[int, str] = {}
        self._retry_at = 0.0
        self._retry_step = 0
        self._sockdir: str | None = None
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    @staticmethod
    def available() -> bool:
        return shutil.which("mpv") is not None

    def start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        if not self.available():
            raise MpvNotInstalled("mpv is not installed (apt install mpv)")

        self._sockdir = tempfile.mkdtemp(prefix="cpzradio-")
        sock_path = os.path.join(self._sockdir, "mpv.sock")
        self.proc = subprocess.Popen(
            self._argv(sock_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        self.ipc = MpvIPC(sock_path, on_event=self._on_event)
        self.ipc.connect()
        for index, name in enumerate(self._OBSERVED, start=1):
            self._prop_names[index] = name
            self.ipc.observe_property(index, name)
        self.ipc.set_property("volume", self._volume)

    def _argv(self, sock_path: str) -> list[str]:
        argv = [
            "mpv",
            "--idle=yes",
            "--no-video",
            "--no-terminal",
            "--really-quiet",
            "--audio-display=no",
            f"--input-ipc-server={sock_path}",
            "--cache=yes",
            "--cache-secs=30",
            "--demuxer-max-bytes=8MiB",
            "--audio-client-name=cardputerzero-radio",
            f"--volume={self._volume}",
            # Let FFmpeg retry transient network drops before mpv gives up.
            "--stream-lavf-o=reconnect=1,reconnect_streamed=1,"
            "reconnect_on_network_error=1,reconnect_delay_max=10",
        ]
        if self._audio_device:
            argv.append(f"--audio-device={self._audio_device}")
        argv.extend(self._extra_args)
        return argv

    def shutdown(self) -> None:
        if self.ipc is not None:
            try:
                self.ipc.command("quit", wait=False)
            except (ConnectionError, OSError):
                pass
            self.ipc.close()
            self.ipc = None
        if self.proc is not None:
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            self.proc = None
        if self._sockdir:
            shutil.rmtree(self._sockdir, ignore_errors=True)
            self._sockdir = None
        self.state = STOPPED

    # -- events ------------------------------------------------------------

    def _on_event(self, message: dict) -> None:
        event = message.get("event")
        if event == "property-change":
            name = self._prop_names.get(message.get("id", -1)) or message.get("name")
            if name:
                self._props[name] = message.get("data")
            return
        if event in ("file-loaded", "playback-restart"):
            with self._lock:
                if self.url:
                    self.state = PLAYING
                    self.error_text = ""
                    self._retry_step = 0
            return
        if event == "end-file":
            reason = message.get("reason", "")
            with self._lock:
                if not self.url or reason == "quit":
                    return
                if reason in ("error", "eof", "unknown"):
                    self.state = ERROR
                    self.error_text = {
                        "error": "stream error",
                        "eof": "stream ended",
                    }.get(reason, "stream stopped")
                    delay = RETRY_BACKOFF[min(self._retry_step, len(RETRY_BACKOFF) - 1)]
                    self._retry_step += 1
                    self._retry_at = time.monotonic() + delay

    # -- transport ---------------------------------------------------------

    def play(self, url: str, name: str = "") -> None:
        self.start()
        with self._lock:
            self.url = url
            self.station_name = name
            self.state = CONNECTING
            self.error_text = ""
            self._retry_step = 0
            self._retry_at = 0.0
        self._props.pop("metadata", None)
        self._props.pop("media-title", None)
        assert self.ipc is not None
        self.ipc.command("loadfile", url, "replace", wait=False)
        if self.recording_path:
            self.ipc.set_property("stream-record", self.recording_path)

    def stop(self) -> None:
        with self._lock:
            self.url = None
            self.station_name = ""
            self.state = STOPPED
            self.error_text = ""
            self._retry_at = 0.0
            self._retry_step = 0
        self.stop_recording()
        if self.ipc is not None:
            self.ipc.command("stop", wait=False)

    def toggle(self, url: str, name: str = "") -> None:
        """Stop if this station is already live, otherwise play it."""
        if self.url == url and self.state in (PLAYING, CONNECTING):
            self.stop()
        else:
            self.play(url, name)

    # -- volume ------------------------------------------------------------

    @property
    def volume(self) -> int:
        return self._volume

    def set_volume(self, value: int) -> int:
        self._volume = max(0, min(100, int(value)))
        if self.ipc is not None:
            self.ipc.set_property("volume", self._volume)
        return self._volume

    # -- recording ---------------------------------------------------------

    def start_recording(self, path: str) -> bool:
        if self.ipc is None or self.state not in (PLAYING, CONNECTING):
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.recording_path = path
        self.ipc.set_property("stream-record", path)
        return True

    def stop_recording(self) -> None:
        if self.recording_path and self.ipc is not None:
            self.ipc.set_property("stream-record", "")
        self.recording_path = None

    @property
    def recording(self) -> bool:
        return self.recording_path is not None

    # -- status ------------------------------------------------------------

    @property
    def now_playing(self) -> str:
        """ICY stream title, when the station provides one."""
        metadata = self._props.get("metadata")
        if isinstance(metadata, dict):
            for key in ("icy-title", "icy-name", "title", "Title"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        title = self._props.get("media-title")
        if isinstance(title, str) and title.strip() and title.strip() != self.url:
            return title.strip()
        return ""

    @property
    def buffered_seconds(self) -> float:
        value = self._props.get("demuxer-cache-time")
        return float(value) if isinstance(value, (int, float)) else 0.0

    def poll(self, now: float | None = None) -> None:
        """Housekeeping: respawn a dead mpv and honour the retry backoff."""
        now = time.monotonic() if now is None else now

        if self.proc is not None and self.proc.poll() is not None:
            # mpv died underneath us - rebuild and resume the current station.
            url, name = self.url, self.station_name
            self.shutdown()
            if url:
                try:
                    self.play(url, name)
                except (MpvNotInstalled, ConnectionError, OSError) as exc:
                    self.state = ERROR
                    self.error_text = str(exc)
            return

        with self._lock:
            retry_due = (
                self.state == ERROR
                and self.url
                and self._retry_at
                and now >= self._retry_at
            )
            url, name = self.url, self.station_name
        if not retry_due:
            return

        with self._lock:
            self.state = CONNECTING
            self._retry_at = 0.0
        try:
            assert self.ipc is not None
            self.ipc.command("loadfile", url, "replace", wait=False)
        except (ConnectionError, OSError, AssertionError):
            with self._lock:
                self.state = ERROR
