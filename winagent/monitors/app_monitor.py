"""Application monitor (TAD 5.1): foreground application and, if enabled, window title.

Title capture is off by default because titles can contain private content (ADR 0005).
A failing sample is skipped and logged without content (TAD 5.1, add-agent-monitor skill).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from winagent.events import Event
from winagent.platform_base import ForegroundWindowReader

log = logging.getLogger("winagent.monitors.app")


class AppMonitor:
    def __init__(
        self,
        reader: ForegroundWindowReader,
        sample_interval_seconds: int,
        include_title: bool = False,
        excluded_apps: frozenset[str] = frozenset(),
        title_keywords_excluded: tuple[str, ...] = (),
    ) -> None:
        self._reader = reader
        self._interval = sample_interval_seconds
        self._include_title = include_title
        self._excluded = frozenset(name.lower() for name in excluded_apps)
        self._title_keywords = tuple(k.lower() for k in title_keywords_excluded if k)

    def sample(self, now: datetime) -> Event | None:
        try:
            info = self._reader.read(include_title=self._include_title)
        except Exception as exc:  # any Windows error: skip this sample, keep the agent running
            log.warning("app sample skipped: %s", type(exc).__name__)
            return None
        if info is None:
            return None
        payload: dict[str, object] = {
            "process_name": info.process_name,
            "duration_seconds": float(self._interval),
        }
        if self._include_title and info.window_title and not self.title_blocked(info.window_title):
            payload["window_title"] = info.window_title[:256]
        return Event(event_type="app_focus", captured_at=now, payload=payload)

    def title_blocked(self, title: str) -> bool:
        """Titles that contain a sensitive keyword are never sent, even when titles are enabled."""
        lowered = title.lower()
        return any(keyword in lowered for keyword in self._title_keywords)

    def set_excluded(self, apps: frozenset[str]) -> None:
        """Called on each policy refresh so the server's exclusion list takes effect."""
        self._excluded = frozenset(name.lower() for name in apps)

    def is_excluded(self, process_name: str | None) -> bool:
        return bool(process_name) and process_name.lower() in self._excluded
