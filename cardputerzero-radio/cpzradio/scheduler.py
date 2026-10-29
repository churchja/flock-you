"""Sleep timer and wake-to-radio alarm.

Both are evaluated from the render loop, so neither needs a thread.  The alarm
only fires while the app is running - enabling autostart is what makes it
dependable, and the UI says so.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta

SLEEP_PRESETS = (15, 30, 45, 60, 90, 120)

WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class SleepTimer:
    """Counts down on the monotonic clock and reports when it lapses."""

    def __init__(self):
        self.deadline: float | None = None
        self.minutes = 0

    @property
    def active(self) -> bool:
        return self.deadline is not None

    def start(self, minutes: int, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.minutes = max(1, int(minutes))
        self.deadline = now + self.minutes * 60

    def cancel(self) -> None:
        self.deadline = None
        self.minutes = 0

    def remaining(self, now: float | None = None) -> int:
        if self.deadline is None:
            return 0
        now = time.monotonic() if now is None else now
        return max(0, int(round(self.deadline - now)))

    def expired(self, now: float | None = None) -> bool:
        """True exactly once, when the timer lapses.  Self-cancels."""
        if self.deadline is None:
            return False
        now = time.monotonic() if now is None else now
        if now < self.deadline:
            return False
        self.cancel()
        return True

    def label(self, now: float | None = None) -> str:
        if not self.active:
            return "off"
        seconds = self.remaining(now)
        return f"{seconds // 60:d}:{seconds % 60:02d}"


@dataclass
class AlarmConfig:
    enabled: bool = False
    hour: int = 7
    minute: int = 0
    url: str = ""
    days: tuple[int, ...] = (0, 1, 2, 3, 4)

    @classmethod
    def from_dict(cls, raw: dict) -> "AlarmConfig":
        raw = raw if isinstance(raw, dict) else {}
        days = raw.get("days")
        if not isinstance(days, (list, tuple)) or not days:
            days = [0, 1, 2, 3, 4]
        return cls(
            enabled=bool(raw.get("enabled", False)),
            hour=max(0, min(23, int(raw.get("hour", 7)))),
            minute=max(0, min(59, int(raw.get("minute", 0)))),
            url=str(raw.get("url") or ""),
            days=tuple(sorted({int(d) % 7 for d in days})),
        )

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "hour": self.hour,
            "minute": self.minute,
            "url": self.url,
            "days": list(self.days),
        }

    @property
    def clock(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"

    @property
    def days_label(self) -> str:
        if not self.days:
            return "never"
        if self.days == (0, 1, 2, 3, 4):
            return "weekdays"
        if self.days == (5, 6):
            return "weekends"
        if len(self.days) == 7:
            return "daily"
        return " ".join(WEEKDAY_LABELS[d][:2] for d in self.days)

    def next_occurrence(self, after: datetime) -> datetime | None:
        """The next datetime this alarm is due, or None when it never is."""
        if not self.enabled or not self.days:
            return None
        for offset in range(8):
            candidate = (after + timedelta(days=offset)).replace(
                hour=self.hour, minute=self.minute, second=0, microsecond=0
            )
            if candidate <= after:
                continue
            if candidate.weekday() in self.days:
                return candidate
        return None


class Alarm:
    """Edge-triggered alarm; fires at most once per scheduled minute."""

    def __init__(self, config: AlarmConfig):
        self.config = config
        self._last_fired: tuple[int, int, int, int, int] | None = None

    def due(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        config = self.config
        if not config.enabled or now.weekday() not in config.days:
            return False
        if (now.hour, now.minute) != (config.hour, config.minute):
            return False
        stamp = (now.year, now.month, now.day, now.hour, now.minute)
        if self._last_fired == stamp:
            return False
        self._last_fired = stamp
        return True

    def countdown_label(self, now: datetime | None = None) -> str:
        now = now or datetime.now()
        nxt = self.config.next_occurrence(now)
        if nxt is None:
            return "off"
        delta = nxt - now
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        return f"in {hours}h{remainder // 60:02d}m"
