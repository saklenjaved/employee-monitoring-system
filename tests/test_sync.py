import uuid
from datetime import datetime, timezone

import pytest

from tests.fake_http import ScriptedSession
from winagent.api_client import AgentApi
from winagent.events import Event
from winagent.http_client import HttpClient
from winagent.queue import MemoryEventQueue
from winagent.sync import MAX_BATCH, SyncManager

BASE = "https://api.example.invalid/api/agent/v1"
T = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


def ev() -> Event:
    return Event("activity_sample", T, {"state": "active", "idle_seconds": 1.0, "sample_interval_seconds": 30.0})


def build(outcomes, queue=None, batch=MAX_BATCH, attempts=5):
    session = ScriptedSession(outcomes)
    api = AgentApi(HttpClient(BASE, 5, lambda: "tok", session=session))
    sleeps: list[float] = []
    mgr = SyncManager(api, queue or MemoryEventQueue(20000), batch_size=batch, max_attempts=attempts,
                      sleep=sleeps.append, clock=lambda: T)
    return mgr, session, sleeps


def test_batches_never_exceed_500_and_all_sent():
    queue = MemoryEventQueue(2000)
    events = [ev() for _ in range(1200)]
    for e in events:
        queue.add(e)
    outcomes = []
    for chunk in (events[0:500], events[500:1000], events[1000:1200]):
        outcomes.append((200, {"accepted": [e.event_id for e in chunk], "duplicates": [], "rejected": []}))
    mgr, session, _ = build(outcomes, queue)
    result = mgr.flush()
    sizes = [len(c["json"]["events"]) for c in session.calls]
    assert sizes == [500, 500, 200]
    assert max(sizes) <= 500
    assert queue.depth() == 0
    assert result.accepted == 1200


def test_failed_batch_is_retried_with_same_event_ids_then_succeeds():
    queue = MemoryEventQueue(100)
    e = ev()
    queue.add(e)
    outcomes = [
        ConnectionError("drop"),
        (503, {}),
        (200, {"accepted": [e.event_id], "duplicates": [], "rejected": []}),
    ]
    from requests import ConnectionError as RC

    outcomes[0] = RC("dropped")
    mgr, session, sleeps = build(outcomes, queue)
    result = mgr.flush()
    ids = [c["json"]["events"][0]["event_id"] for c in session.calls]
    assert ids == [e.event_id] * 3
    assert queue.depth() == 0
    assert result.attempts == 3
    assert len(sleeps) == 2


def test_backoff_is_exponential_and_capped():
    mgr, _, _ = build([])
    assert mgr.backoff_for(0) < mgr.backoff_for(1) < mgr.backoff_for(2)
    assert mgr.backoff_for(30) <= 300 * 1.1 + 1e-9


def test_gives_up_after_max_attempts_and_keeps_events_queued():
    queue = MemoryEventQueue(100)
    queue.add(ev())
    outcomes = [(500, {})] * 5
    mgr, session, _ = build(outcomes, queue, attempts=5)
    result = mgr.flush()
    assert result.gave_up is True
    assert queue.depth() == 1
    assert len(session.calls) == 5


def test_auth_error_stops_sync_and_does_not_retry():
    queue = MemoryEventQueue(100)
    queue.add(ev())
    mgr, session, _ = build([(401, {})], queue)
    result = mgr.flush()
    assert result.auth_failed is True
    assert len(session.calls) == 1
    assert queue.depth() == 1
    # A later flush does nothing until re-registration.
    assert mgr.flush().auth_failed is True
    assert len(session.calls) == 1


def test_permanent_error_drops_batch_so_queue_is_not_blocked():
    queue = MemoryEventQueue(100)
    queue.add(ev())
    mgr, _, _ = build([(400, {"code": "bad"})], queue)
    result = mgr.flush()
    assert queue.depth() == 0
    assert result.rejected == 1


def test_rejected_and_duplicate_ids_are_acked():
    queue = MemoryEventQueue(100)
    a, b = ev(), ev()
    queue.add(a)
    queue.add(b)
    mgr, _, _ = build([(200, {"accepted": [], "duplicates": [a.event_id], "rejected": [b.event_id]})], queue)
    result = mgr.flush()
    assert queue.depth() == 0
    assert (result.duplicates, result.rejected) == (1, 1)


def test_unacknowledged_events_stay_queued():
    queue = MemoryEventQueue(100)
    a = ev()
    queue.add(a)
    mgr, _, _ = build([(200, {"accepted": [], "duplicates": [], "rejected": []})], queue)
    mgr.flush()
    assert queue.depth() == 1


def test_last_successful_sync_recorded():
    queue = MemoryEventQueue(100)
    e = ev()
    queue.add(e)
    mgr, _, _ = build([(200, {"accepted": [e.event_id], "duplicates": [], "rejected": []})], queue)
    assert mgr.last_successful_sync_at is None
    mgr.flush()
    assert mgr.last_successful_sync_at == T


def test_batch_size_out_of_range_rejected():
    with pytest.raises(ValueError):
        build([], batch=501)


def test_event_ids_are_uuid4():
    assert uuid.UUID(ev().event_id).version == 4
