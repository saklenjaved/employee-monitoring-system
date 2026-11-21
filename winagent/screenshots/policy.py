"""Policy checks that must pass before any capture (TAD 6, 9; screenshot-policy rule).

Order matters: the first failing check returns a skip reason, and nothing is captured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from winagent.schedule import MonitoringWindow


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class ScreenshotPolicy:
    enabled: bool = False  # off by default (POC default)
    interval_seconds: int = 600
    window: MonitoringWindow | None = None
    excluded_apps: frozenset[str] = frozenset()
    blur_regions: tuple[Rect, ...] = field(default_factory=tuple)

    def skip_reason(
        self,
        local_now: datetime,
        session_type: str,
        locked: bool,
        foreground_process: str | None,
        local_enabled: bool,
    ) -> str | None:
        if not local_enabled:
            return "local_config_disabled"
        if not self.enabled:
            return "policy_disabled"
        if self.window is None or not self.window.is_open(local_now):
            return "outside_monitoring_hours"
        if session_type != "console":
            return "unsupported_session"
        if locked:
            return "session_locked"
        if foreground_process and foreground_process.lower() in {a.lower() for a in self.excluded_apps}:
            return "excluded_application"
        return None
