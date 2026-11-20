"""Local health snapshot (TAD 5.1 logging & health). Written atomically; contains no secrets or content."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class HealthSnapshot:
    agent_version: str
    monitoring_state: str = "starting"
    session_type: str = "unknown"
    last_successful_sync_at: str | None = None
    last_error_code: str | None = None
    queue_depth: int = 0
    dropped_events: int = 0
    policy_version: str | None = None
    screenshots_enabled: bool = False
    connected: bool = False  # last sync or heartbeat reached the backend

    def set_sync_time(self, when: datetime | None) -> None:
        self.last_successful_sync_at = when.isoformat().replace("+00:00", "Z") if when else None


def write_health(path: Path, snapshot: HealthSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(snapshot), indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_health(path: Path) -> HealthSnapshot | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return HealthSnapshot(**data)


def status_line(snapshot: HealthSnapshot) -> str:
    """Short text for the tray tooltip. Shows state only, never content."""
    link = "Connected" if snapshot.connected else "Not connected"
    labels = {
        "active": "Monitoring active",
        "awaiting_pairing": "Waiting for approval on the dashboard",
        "awaiting_start": "Waiting for start from the dashboard",
        "paused_hours": "Outside monitoring hours",
        "paused_policy": "Monitoring paused by policy",
        "unsupported_session": "Session type not supported",
        "not_registered": "Not registered",
        "needs_registration": "Re-registration required",
        "starting": "Starting",
    }
    state = labels.get(snapshot.monitoring_state, snapshot.monitoring_state)
    return f"{state} | {link}"
