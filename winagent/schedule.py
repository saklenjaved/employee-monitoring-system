"""Monitoring hours (TAD 6). Days are Monday=0 to Sunday=6.

The hours are in the organization's time zone, sent with the policy ("timezone"), the same way the server
checks them before it accepts a screenshot. Without a known time zone, the device's own time is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class MonitoringWindow:
    days: frozenset[int]
    start: time
    end: time
    enabled: bool = True
    # IANA name such as "UTC" or "Asia/Kolkata". Empty means the device's local time.
    timezone: str = ""

    @classmethod
    def from_policy(cls, monitoring: dict) -> "MonitoringWindow":
        return cls(
            days=frozenset(int(d) for d in monitoring.get("days", [])),
            start=_parse_hhmm(monitoring.get("start", "00:00")),
            end=_parse_hhmm(monitoring.get("end", "00:00")),
            enabled=bool(monitoring.get("enabled", False)),
            timezone=str(monitoring.get("timezone") or ""),
        )

    def is_open(self, now: datetime) -> bool:
        """True only when monitoring is enabled, today is an allowed day, and the time is in the window.

        `now` is a time-zone-aware moment. It is read in the policy's time zone, so the agent and the server
        agree on when monitoring is open, whatever time zone the PC is set to.
        """
        local = self._in_policy_zone(now)
        if not self.enabled or local.weekday() not in self.days:
            return False
        current = local.time().replace(tzinfo=None)
        if self.start <= self.end:
            return self.start <= current < self.end
        # Window crosses midnight, for example 22:00 to 06:00.
        return current >= self.start or current < self.end

    def _in_policy_zone(self, now: datetime) -> datetime:
        if not self.timezone or now.tzinfo is None:
            return now
        try:
            return now.astimezone(ZoneInfo(self.timezone))
        except (ZoneInfoNotFoundError, ValueError):
            # Unknown name, or no time zone data on this PC: keep the device time, as before.
            return now


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
