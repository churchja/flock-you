"""Display, keyboard and main-loop plumbing for CardputerZero apps.

Three interchangeable backends render the same PIL image:

``FramebufferBackend``
    The real device.  Writes packed pixels straight to the LCD framebuffer and
    reads the built-in keyboard through evdev.
``TkBackend``
    A desktop simulator so the UI can be developed without hardware.
``HeadlessBackend``
    Renders frames to PNG files.  Used by the smoke test and by CI.

The device is a 16bpp RGB565 panel, but 32bpp is handled too because the exact
framebuffer format of a stock image cannot be probed from here.
"""

from __future__ import annotations

import glob
import mmap
import os
import select
import struct
import sys
import time
from dataclasses import dataclass

from . import theme

# linux/fb.h
FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602

# The stock image exposes the LCD under a stable alias; LV_LINUX_FBDEV_DEVICE is
# what the vendor's own LVGL apps honour, so we accept it as an override too.
FB_CANDIDATES = ("/dev/fb_lcd", "/dev/fb0", "/dev/fb1")
FB_ENV_VARS = ("CPZRADIO_FBDEV", "APPLAUNCH_LINUX_FBDEV_DEVICE", "LV_LINUX_FBDEV_DEVICE")

# The CP0 matrix keyboard is an I2C device; this by-path node is what the
# vendor's binaries reference.  Globbed, because the platform address could
# differ across board revisions.
KBD_ENV_VARS = ("CPZRADIO_KBD", "LV_LINUX_KEYBOARD_DEVICE")
KBD_BY_PATH_GLOBS = (
    "/dev/input/by-path/*i2c*-event*",
    "/dev/input/by-path/*-event-kbd",
)


@dataclass(frozen=True)
class KeyEvent:
    """A single key transition, normalised across backends."""

    name: str
    char: str = ""
    pressed: bool = True
    repeat: bool = False

    @property
    def down(self) -> bool:
        return self.pressed


# ---------------------------------------------------------------------------
# key naming
# ---------------------------------------------------------------------------

# Names are lowercase and backend-independent so screen code never imports evdev.
_SPECIAL_KEYS = {
    "ESC": "escape",
    "ENTER": "enter",
    "KPENTER": "enter",
    "BACKSPACE": "backspace",
    "DELETE": "delete",
    "TAB": "tab",
    "SPACE": "space",
    "UP": "up",
    "DOWN": "down",
    "LEFT": "left",
    "RIGHT": "right",
    "LEFTSHIFT": "shift",
    "RIGHTSHIFT": "shift",
    "LEFTCTRL": "ctrl",
    "RIGHTCTRL": "ctrl",
    "LEFTALT": "alt",
    "RIGHTALT": "alt",
    "FN": "fn",
    "CAPSLOCK": "capslock",
    "MINUS": "-",
    "EQUAL": "=",
    "COMMA": ",",
    "DOT": ".",
    "SLASH": "/",
    "SEMICOLON": ";",
    "APOSTROPHE": "'",
    "GRAVE": "`",
    "BACKSLASH": "\\",
    "LEFTBRACE": "[",
    "RIGHTBRACE": "]",
}

# US layout shift pairs, needed for typing search queries on the CP0.
_SHIFT_PAIRS = {
    "1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
    "6": "^", "7": "&", "8": "*", "9": "(", "0": ")",
    "-": "_", "=": "+", "[": "{", "]": "}", "\\": "|",
    ";": ":", "'": '"', ",": "<", ".": ">", "/": "?", "`": "~",
}

MODIFIERS = frozenset({"shift", "ctrl", "alt", "fn", "capslock"})


def key_name_from_evdev(code_name: str) -> str:
    """Map an evdev ``KEY_*`` constant name onto our normalised name."""
    if not code_name.startswith("KEY_"):
        return ""
    stem = code_name[4:]
    if stem in _SPECIAL_KEYS:
        return _SPECIAL_KEYS[stem]
    if len(stem) == 1 and (stem.isalpha() or stem.isdigit()):
        return stem.lower()
    return ""


def char_for(name: str, shift: bool) -> str:
    """Printable character produced by ``name``, or '' for non-typing keys."""
    if name == "space":
        return " "
    if len(name) != 1:
        return ""
    if name.isalpha():
        return name.upper() if shift else name
    if shift:
        return _SHIFT_PAIRS.get(name, name)
    return name


# ---------------------------------------------------------------------------
# pixel packing
# ---------------------------------------------------------------------------

try:  # numpy makes the per-frame conversion cheap enough for a Pi Zero class CPU
    import numpy as _np
except ImportError:  # pragma: no cover - exercised only on numpy-less systems
    _np = None

# Fallback lookup tables (pure Python path).
_R5 = tuple((v >> 3) << 11 for v in range(256))
_G6 = tuple((v >> 2) << 5 for v in range(256))
_B5 = tuple(v >> 3 for v in range(256))


def pack_rgb565(image) -> bytes:
    """Convert an RGB PIL image to little-endian RGB565 bytes."""
    if image.mode != "RGB":
        image = image.convert("RGB")

    if _np is not None:
        arr = _np.asarray(image, dtype=_np.uint8).astype(_np.uint16)
        packed = (
            ((arr[:, :, 0] >> 3) << 11)
            | ((arr[:, :, 1] >> 2) << 5)
            | (arr[:, :, 2] >> 3)
        )
        return packed.astype("<u2").tobytes()

    raw = image.tobytes()
    out = bytearray(len(raw) // 3 * 2)
    for i in range(0, len(raw), 3):
        value = _R5[raw[i]] | _G6[raw[i + 1]] | _B5[raw[i + 2]]
        j = i // 3 * 2
        out[j] = value & 0xFF
        out[j + 1] = value >> 8
    return bytes(out)


def pack_xrgb8888(image) -> bytes:
    """Convert an RGB PIL image to 32bpp BGRX, the usual Linux fb layout."""
    if image.mode != "RGB":
        image = image.convert("RGB")

    if _np is not None:
        arr = _np.asarray(image, dtype=_np.uint8)
        out = _np.zeros((arr.shape[0], arr.shape[1], 4), dtype=_np.uint8)
        out[:, :, 0] = arr[:, :, 2]  # B
        out[:, :, 1] = arr[:, :, 1]  # G
        out[:, :, 2] = arr[:, :, 0]  # R
        out[:, :, 3] = 0xFF
        return out.tobytes()

    raw = image.tobytes()
    out = bytearray(len(raw) // 3 * 4)
    for i in range(0, len(raw), 3):
        j = i // 3 * 4
        out[j] = raw[i + 2]
        out[j + 1] = raw[i + 1]
        out[j + 2] = raw[i]
        out[j + 3] = 0xFF
    return bytes(out)


# ---------------------------------------------------------------------------
# backends
# ---------------------------------------------------------------------------


class Backend:
    """Interface implemented by every backend."""

    width = theme.WIDTH
    height = theme.HEIGHT

    def poll(self) -> list[KeyEvent]:
        return []

    def present(self, image) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class FramebufferBackend(Backend):
    """Real hardware: mmapped framebuffer plus evdev keyboard."""

    def __init__(self, fb_path: str | None = None, kbd_path: str | None = None):
        self.path = fb_path or resolve_fbdev()
        if not self.path:
            raise RuntimeError("no framebuffer device found")

        self.fd = os.open(self.path, os.O_RDWR)
        try:
            self.width, self.height, self.bpp = self._read_var()
            self.line_length, smem_len = self._read_fix()
            self.mm = mmap.mmap(
                self.fd, smem_len, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE
            )
        except Exception:
            os.close(self.fd)
            raise

        if self.bpp == 16:
            self._pack = pack_rgb565
            self._stride = self.width * 2
        elif self.bpp == 32:
            self._pack = pack_xrgb8888
            self._stride = self.width * 4
        else:
            self.mm.close()
            os.close(self.fd)
            raise RuntimeError(f"unsupported framebuffer depth: {self.bpp} bpp")

        self._keyboard = EvdevKeyboard(kbd_path)

    def _read_var(self) -> tuple[int, int, int]:
        import fcntl

        buf = bytearray(160)
        fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, buf)
        xres, yres, _xv, _yv, _xo, _yo, bpp = struct.unpack_from("7I", buf, 0)
        return xres, yres, bpp

    def _read_fix(self) -> tuple[int, int]:
        import fcntl

        fmt = "16sL" + "I" * 6 + "HHHI"
        buf = bytearray(struct.calcsize(fmt))
        fcntl.ioctl(self.fd, FBIOGET_FSCREENINFO, buf)
        values = struct.unpack(fmt, buf)
        smem_len = values[2]
        line_length = values[8]
        return line_length, smem_len

    def poll(self) -> list[KeyEvent]:
        return self._keyboard.poll()

    def present(self, image) -> None:
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height))
        data = self._pack(image)
        if self.line_length == self._stride:
            self.mm.seek(0)
            self.mm.write(data)
            return
        # Padded scanlines: copy row by row.
        for y in range(self.height):
            start = y * self._stride
            self.mm.seek(y * self.line_length)
            self.mm.write(data[start : start + self._stride])

    def close(self) -> None:
        self._keyboard.close()
        try:
            self.mm.close()
        finally:
            os.close(self.fd)


class EvdevKeyboard:
    """Reads the CP0 keyboard, tracking shift so we can emit real characters."""

    def __init__(self, path: str | None = None):
        self.devices: list = []
        self.shift = False
        self._ecodes = None
        try:
            from evdev import InputDevice, ecodes, list_devices
        except ImportError:
            return

        self._ecodes = ecodes
        for candidate in self._candidates(path, list_devices):
            try:
                dev = InputDevice(candidate)
            except (OSError, PermissionError):
                continue
            caps = dev.capabilities().get(ecodes.EV_KEY, [])
            if ecodes.KEY_ENTER in caps or ecodes.KEY_ESC in caps:
                self.devices.append(dev)
            else:
                dev.close()

    @staticmethod
    def _candidates(path: str | None, list_devices) -> list[str]:
        if path:
            return [path]
        for var in KBD_ENV_VARS:
            value = os.environ.get(var)
            if value:
                return [value]
        found: list[str] = []
        for pattern in KBD_BY_PATH_GLOBS:
            found.extend(sorted(glob.glob(pattern)))
        # Fall back to scanning every input node.
        return found or sorted(list_devices())

    def poll(self) -> list[KeyEvent]:
        if not self.devices:
            return []
        ecodes = self._ecodes
        fds = [d.fd for d in self.devices]
        readable, _, _ = select.select(fds, [], [], 0)
        events: list[KeyEvent] = []
        for dev in self.devices:
            if dev.fd not in readable:
                continue
            try:
                raw_events = list(dev.read())
            except OSError:
                continue
            for raw in raw_events:
                if raw.type != ecodes.EV_KEY:
                    continue
                label = ecodes.KEY.get(raw.code)
                if isinstance(label, (list, tuple)):
                    label = label[0]
                if not label:
                    continue
                name = key_name_from_evdev(label)
                if not name:
                    continue
                pressed = raw.value != 0
                if name == "shift":
                    self.shift = pressed
                    continue
                if not pressed:
                    events.append(KeyEvent(name, "", False, False))
                    continue
                events.append(
                    KeyEvent(name, char_for(name, self.shift), True, raw.value == 2)
                )
        return events

    def close(self) -> None:
        for dev in self.devices:
            try:
                dev.close()
            except OSError:
                pass
        self.devices = []


class TkBackend(Backend):
    """Desktop simulator so the UI can be built without a CardputerZero."""

    _TK_NAMES = {
        "Escape": "escape",
        "Return": "enter",
        "BackSpace": "backspace",
        "Delete": "delete",
        "Tab": "tab",
        "space": "space",
        "Up": "up",
        "Down": "down",
        "Left": "left",
        "Right": "right",
        "Shift_L": "shift",
        "Shift_R": "shift",
        "Control_L": "ctrl",
        "Control_R": "ctrl",
        "Alt_L": "alt",
        "Alt_R": "alt",
    }

    def __init__(self, scale: int = 3, title: str = "CardputerZero"):
        import tkinter as tk

        self._tk = tk
        self.scale = max(1, scale)
        self._pending: list[KeyEvent] = []
        self._photo = None
        self.closed = False

        self.root = tk.Tk()
        self.root.title(f"{title} - CP0 simulator")
        self.root.resizable(False, False)
        self.canvas = tk.Canvas(
            self.root,
            width=self.width * self.scale,
            height=self.height * self.scale,
            bg=theme.BG,
            highlightthickness=0,
        )
        self.canvas.pack()
        self.root.bind_all("<KeyPress>", lambda e: self._on_key(e, True))
        self.root.bind_all("<KeyRelease>", lambda e: self._on_key(e, False))
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self) -> None:
        self.closed = True

    def _on_key(self, event, pressed: bool) -> None:
        name = self._TK_NAMES.get(event.keysym)
        if name is None:
            sym = event.keysym
            name = sym.lower() if len(sym) == 1 else ""
        if not name:
            return
        char = event.char if event.char and event.char.isprintable() else ""
        if name == "space":
            char = " "
        self._pending.append(KeyEvent(name, char if pressed else "", pressed, False))

    def poll(self) -> list[KeyEvent]:
        try:
            self.root.update()
        except self._tk.TclError:
            # Window went away underneath us; let the loop shut down cleanly.
            self.closed = True
            return []
        events, self._pending = self._pending, []
        return events

    def present(self, image) -> None:
        import base64
        import io

        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PPM")
        photo = self._tk.PhotoImage(data=base64.b64encode(buf.getvalue()))
        if self.scale > 1:
            photo = photo.zoom(self.scale, self.scale)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=photo, anchor="nw")
        self._photo = photo  # keep a reference alive or Tk drops the image
        self.root.update_idletasks()

    def close(self) -> None:
        try:
            self.root.destroy()
        except Exception:
            pass


class HeadlessBackend(Backend):
    """Captures frames as PNGs; used for smoke tests and CI."""

    def __init__(self, outdir: str | None = None):
        self.outdir = outdir
        self.frames = 0
        self.last = None
        if outdir:
            os.makedirs(outdir, exist_ok=True)

    def present(self, image) -> None:
        self.last = image.copy()
        if self.outdir:
            image.save(os.path.join(self.outdir, f"frame{self.frames:03d}.png"))
        self.frames += 1


# ---------------------------------------------------------------------------
# selection + loop
# ---------------------------------------------------------------------------


def resolve_fbdev() -> str | None:
    for var in FB_ENV_VARS:
        value = os.environ.get(var)
        if value and os.path.exists(value):
            return value
    for path in FB_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def pick_backend(prefer: str = "auto", **kwargs) -> Backend:
    """Choose a backend.  ``prefer`` is one of auto/fb/tk/headless."""
    if prefer == "fb":
        return FramebufferBackend()
    if prefer == "tk":
        return TkBackend(**kwargs)
    if prefer == "headless":
        return HeadlessBackend(**kwargs)

    if sys.platform.startswith("linux") and resolve_fbdev():
        try:
            return FramebufferBackend()
        except Exception as exc:  # noqa: BLE001 - fall back rather than die
            print(f"[cpzradio] framebuffer unavailable ({exc}); using simulator")
    return TkBackend(**kwargs)


def run(app, backend: Backend, fps: int = 20) -> int:
    """Drive ``app`` until it stops.  Returns a process exit code."""
    frame_time = 1.0 / max(1, fps)
    try:
        while app.running:
            if getattr(backend, "closed", False):
                break
            started = time.monotonic()
            for event in backend.poll():
                app.on_key(event)
            app.update(started)
            backend.present(app.draw())
            slack = frame_time - (time.monotonic() - started)
            if slack > 0:
                time.sleep(slack)
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()
        backend.close()
    return 0
