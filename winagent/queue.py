"""Bounded event queue behind an interface (TAD 5.1, 5.2, 11).

The POC uses the in-memory implementation only. Offline buffering is not approved (TAD 13),
so events are lost on process exit. This is a recorded gap (docs/open-decisions.md, ADR 0003).
A SQLite implementation can be added behind the same Protocol later, once approved.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Protocol

from winagent.events import Event


class EventQueue(Protocol):
    def add(self, event: Event) -> bool: ...

    def peek(self, limit: int) -> list[Event]: ...

    def ack(self, event_ids: set[str]) -> int: ...

    def depth(self) -> int: ...

    def dropped_count(self) -> int: ...


class MemoryEventQueue:
    """FIFO queue with a hard size cap. When full, the oldest event is dropped and counted.

    Duplicate event_ids that are still queued are ignored, so one event cannot be sent twice from here.
    """

    def __init__(self, max_events: int) -> None:
        if max_events < 1:
            raise ValueError("max_events must be at least 1")
        self._max = max_events
        self._items: OrderedDict[str, Event] = OrderedDict()
        self._dropped = 0

    def add(self, event: Event) -> bool:
        if event.event_id in self._items:
            return False
        if len(self._items) >= self._max:
            self._items.popitem(last=False)
            self._dropped += 1
        self._items[event.event_id] = event
        return True

    def peek(self, limit: int) -> list[Event]:
        out: list[Event] = []
        for event in self._items.values():
            if len(out) >= limit:
                break
            out.append(event)
        return out

    def ack(self, event_ids: set[str]) -> int:
        removed = 0
        for event_id in event_ids:
            if self._items.pop(event_id, None) is not None:
                removed += 1
        return removed

    def depth(self) -> int:
        return len(self._items)

    def dropped_count(self) -> int:
        return self._dropped
