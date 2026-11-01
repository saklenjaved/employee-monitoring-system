"""Network recovery (TAD 12): drops, 5xx, recovery after the connection returns, no duplicates.

The fake server keeps every event_id it has stored and reports repeats as duplicates,
as the backend is expected to do. The test then checks that the stored set has no repeats.
"""

from datetime import datetime, timezone

import requests

from tests.fake_http import FakeResponse
from winagent.api_client import AgentApi
from winagent.events import Event
from winagent.http_client import HttpClient
from winagent.queue import MemoryEventQueue
from winagent.sync import SyncManager

BASE = "https://api.example.invalid/api/agent/v1"
T = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


class FlakyBackend:
    """Drops the first `drops` connections, then answers. Stores event_ids and reports duplicates."""

    def __init__(self, drops: int, server_error_then_ok: bool = False) -> None:
        self.drops = drops
        self.server_error_then_ok = server_error_then_ok
        self.stored: list[str] = []
        self.seen: set[str] = set()
        self.requests = 0

    def request(self, method, url, **kwargs):
        self.requests += 1
        if self.drops > 0:
            self.drops -= 1
            raise requests.ConnectionError("connection reset")
        if self.server_error_then_ok:
            self.server_error_then_ok = False
            return FakeResponse(502, {})
        ids = [e["event_id"] for e in kwargs["json"]["events"]]
        accepted, duplicates = [], []
        for event_id in ids:
            if event_id in self.seen:
                duplicates.append(event_id)
            else:
                self.seen.add(event_id)
                self.stored.append(event_id)
                accepted.append(event_id)
        return FakeResponse(200, {"accepted": accepted, "duplicates": duplicates, "rejected": []})


def run(backend, events):
    queue = MemoryEventQueue(1000)
    for e in events:
        queue.add(e)
    api = AgentApi(HttpClient(BASE, 5, lambda: "tok", session=backend))
    sleeps: list[float] = []
    mgr = SyncManager(api, queue, max_attempts=6, sleep=sleeps.append, clock=lambda: T)
    result = mgr.flush()
    return queue, mgr, result, sleeps


def ev() -> Event:
    return Event("activity_sample", T, {"state": "idle", "idle_seconds": 400.0, "sample_interval_seconds": 30.0})


def test_recovers_after_connection_drops_without_duplicates():
    backend = FlakyBackend(drops=3)
    events = [ev() for _ in range(10)]
    queue, mgr, result, sleeps = run(backend, events)
    assert queue.depth() == 0
    assert len(backend.stored) == 10
    assert len(set(backend.stored)) == len(backend.stored)
    assert len(sleeps) == 3


def test_server_error_then_success_is_not_duplicated():
    backend = FlakyBackend(drops=0, server_error_then_ok=True)
    events = [ev() for _ in range(3)]
    queue, _, _, _ = run(backend, events)
    assert sorted(backend.stored) == sorted(e.event_id for e in events)


def test_resend_after_lost_response_is_reported_as_duplicate():
    # The server stores the batch but the response is lost. The agent retries with the same ids.
    class LostResponse(FlakyBackend):
        def request(self, method, url, **kwargs):
            if self.drops == 1:
                self.drops = 0
                ids = [e["event_id"] for e in kwargs["json"]["events"]]
                self.stored.extend(ids)
                self.seen.update(ids)
                raise requests.ConnectionError("response lost")
            return super().request(method, url, **kwargs)

    backend = LostResponse(drops=1)
    events = [ev() for _ in range(4)]
    queue, _, result, _ = run(backend, events)
    assert queue.depth() == 0
    assert len(backend.stored) == len(set(backend.stored)) == 4
    assert result.duplicates == 4


def test_offline_period_keeps_events_then_flushes_on_return():
    backend = FlakyBackend(drops=100)
    events = [ev() for _ in range(5)]
    queue = MemoryEventQueue(1000)
    for e in events:
        queue.add(e)
    api = AgentApi(HttpClient(BASE, 5, lambda: "tok", session=backend))
    mgr = SyncManager(api, queue, max_attempts=3, sleep=lambda s: None, clock=lambda: T)
    mgr.flush()
    assert queue.depth() == 5  # nothing lost while offline
    backend.drops = 0
    mgr.flush()
    assert queue.depth() == 0
    assert len(set(backend.stored)) == 5
