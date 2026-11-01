"""Activity monitor (TAD 5.1, 5.2): active vs. idle from GetLastInputInfo only.

Only the time since last input is read. The monitor never sees which key was pressed
(privacy rule). Idle threshold default is 300 s, sample interval default is 30 s (POC defaults).
"""

from __future__ import annotations

import logging
from datetime import datetime

from winagent.events import Event
from winagent.platform_base import IdleTimeReader

log = logging.getLogger("winagent.monitors.activity")


class ActivityMonitor:
    def __init__(self, reader: IdleTimeReader, idle_threshold_seconds: int, sample_interval_seconds: int) -> None:
        self._reader = reader
        self._threshold = idle_threshold_seconds
        self._interval = sample_interval_seconds

    def classify(self, idle_seconds: float) -> str:
        return "idle" if idle_seconds >= self._threshold else "active"

    def sample(self, now: datetime) -> Event | None:
        try:
            idle = float(self._reader.idle_seconds())
        except Exception as exc:  # skip this sample, keep the agent running
            log.warning("activity sample skipped: %s", type(exc).__name__)
            return None
        idle = max(idle, 0.0)
        return Event(
            event_type="activity_sample",
            captured_at=now,
            payload={
                "state": self.classify(idle),
                "idle_seconds": round(idle, 3),
                "sample_interval_seconds": float(self._interval),
            },
        )
