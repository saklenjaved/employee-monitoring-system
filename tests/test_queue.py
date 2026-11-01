from datetime import datetime, timezone

from winagent.events import Event
from winagent.queue import MemoryEventQueue

T = datetime(2026, 11, 1, tzinfo=timezone.utc)


def ev() -> Event:
    return Event("activity_sample", T, {"state": "active", "idle_seconds": 0.0, "sample_interval_seconds": 30.0})


def test_fifo_and_peek_limit():
    q = MemoryEventQueue(10)
    events = [ev() for _ in range(5)]
    for e in events:
        q.add(e)
    assert [e.event_id for e in q.peek(3)] == [e.event_id for e in events[:3]]
    assert q.depth() == 5


def test_duplicate_event_id_ignored_while_queued():
    q = MemoryEventQueue(10)
    e = ev()
    assert q.add(e) is True
    assert q.add(e) is False
    assert q.depth() == 1


def test_bounded_drops_oldest_and_counts():
    q = MemoryEventQueue(3)
    events = [ev() for _ in range(5)]
    for e in events:
        q.add(e)
    assert q.depth() == 3
    assert q.dropped_count() == 2
    assert [e.event_id for e in q.peek(10)] == [e.event_id for e in events[2:]]


def test_ack_removes_only_listed_ids():
    q = MemoryEventQueue(10)
    a, b = ev(), ev()
    q.add(a)
    q.add(b)
    assert q.ack({a.event_id}) == 1
    assert [e.event_id for e in q.peek(10)] == [b.event_id]


def test_ack_unknown_ids_is_safe():
    q = MemoryEventQueue(10)
    assert q.ack({"not-there"}) == 0
