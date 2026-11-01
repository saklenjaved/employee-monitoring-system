"""Event model (TAD 8). Shape is defined in docs/event-schema.json.

Every event has a UUID4 event_id made on the agent, and a UTC timestamp.
Nothing here can carry typed text, and organization or employee ids are never added.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

EVENT_TYPES = frozenset({"app_focus", "activity_sample", "website_visit", "session_state"})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(ts: datetime) -> str:
    """ISO 8601 UTC with a Z suffix, as the schema requires."""
    if ts.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class Event:
    event_type: str
    captured_at: datetime
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event_type: {self.event_type}")
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        if uuid.UUID(self.event_id).version != 4:
            raise ValueError("event_id must be a UUID4")


def to_wire(event: Event) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "captured_at": format_utc(event.captured_at),
        "payload": dict(event.payload),
    }


def from_wire(data: dict[str, Any]) -> Event:
    """Rebuild an Event from its wire form (used when reading the offline queue)."""
    captured = datetime.fromisoformat(str(data["captured_at"]).replace("Z", "+00:00"))
    return Event(
        event_type=data["event_type"],
        captured_at=captured,
        payload=dict(data["payload"]),
        event_id=data["event_id"],
    )
