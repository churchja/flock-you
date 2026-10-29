"""Optional boot-to-radio, toggled from inside the app.

A *user* systemd unit is used deliberately: enabling it needs no root, so the
toggle works from the app without sudo.  The package ships the unit in
/usr/lib/systemd/user; when running from a git checkout we drop an equivalent
copy into ~/.config/systemd/user instead.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

UNIT_NAME = "cardputerzero-radio.service"
PACKAGED_UNIT = Path("/usr/lib/systemd/user") / UNIT_NAME
USER_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"

UNIT_TEMPLATE = """\
[Unit]
Description=CardputerZero Radio
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def _systemctl(*args: str, timeout: float = 8.0) -> tuple[int, str]:
    if not shutil.which("systemctl"):
        return 127, "systemctl not found"
    try:
        result = subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return result.returncode, (result.stdout + result.stderr).strip()


def default_exec_start() -> str:
    """Command systemd should run, matching how we were launched."""
    if PACKAGED_UNIT.exists():
        return "/usr/share/APPLaunch/bin/cardputerzero-radio"
    import sys

    entry = Path(__file__).resolve().parent.parent / "app.py"
    return f"{sys.executable} {entry}"


def ensure_unit() -> Path:
    """Return a usable unit path, writing a fallback one if needed."""
    if PACKAGED_UNIT.exists():
        return PACKAGED_UNIT
    USER_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    target = USER_UNIT_DIR / UNIT_NAME
    target.write_text(
        UNIT_TEMPLATE.format(exec_start=default_exec_start()), encoding="utf-8"
    )
    _systemctl("daemon-reload")
    return target


def is_enabled() -> bool:
    code, output = _systemctl("is-enabled", UNIT_NAME)
    return code == 0 and output.startswith("enabled")


def available() -> bool:
    return shutil.which("systemctl") is not None


def set_enabled(enabled: bool) -> tuple[bool, str]:
    """Enable or disable boot-to-radio.  Returns ``(ok, message)``."""
    if not available():
        return False, "systemd not available"
    if enabled:
        ensure_unit()
        code, output = _systemctl("enable", UNIT_NAME)
        if code != 0:
            return False, output.splitlines()[-1] if output else "enable failed"
        # Lingering lets the unit start at boot without an interactive login.
        # It usually needs privileges, so treat failure as non-fatal.
        _linger_best_effort()
        return True, "autostart on"
    code, output = _systemctl("disable", UNIT_NAME)
    if code != 0:
        return False, output.splitlines()[-1] if output else "disable failed"
    return True, "autostart off"


def _linger_best_effort() -> None:
    if not shutil.which("loginctl"):
        return
    import getpass

    try:
        subprocess.run(
            ["loginctl", "enable-linger", getpass.getuser()],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def status_label() -> str:
    if not available():
        return "unavailable"
    return "on" if is_enabled() else "off"
