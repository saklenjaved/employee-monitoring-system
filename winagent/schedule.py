"""Monitoring hours (TAD 6). Days are Monday=0 to Sunday=6, times are local device time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Iterable


@dataclass(frozen=True)
class MonitoringWindow:
    days: frozenset[int]
    start: time
    end: time
    enabled: bool = True

    @classmethod
    def from_policy(cls, monitoring: dict) -> "MonitoringWindow":
        return cls(
            days=frozenset(int(d) for d in monitoring.get("days", [])),
            start=_parse_hhmm(monitoring.get("start", "00:00")),
            end=_parse_hhmm(monitoring.get("end", "00:00")),
            enabled=bool(monitoring.get("enabled", False)),
        )

    def is_open(self, local_now: datetime) -> bool:
        """True only when monitoring is enabled, today is an allowed day, and the time is in the window."""
        if not self.enabled or local_now.weekday() not in self.days:
            return False
        current = local_now.time().replace(tzinfo=None)
        if self.start <= self.end:
            return self.start <= current < self.end
        # Window crosses midnight, for example 22:00 to 06:00.
        return current >= self.start or current < self.end


def _parse_hhmm(value: str) -> time:
    try:
        hour, minute = (int(part) for part in value.split(":"))
        return time(hour, minute)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"time must be HH:MM, got {value!r}") from exc


def local_now(now_utc: datetime) -> datetime:
    return now_utc.astimezone()


def window_days(days: Iterable[int]) -> frozenset[int]:
    return frozenset(int(d) for d in days)
