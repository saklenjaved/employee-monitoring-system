"""Performance checks (TAD 12 'Performance'). Generous limits so CI does not flake. Real CPU and memory numbers are in docs/test-plan.md (manual P-items)."""

import time
from datetime import datetime, timezone

from tests.fake_http import ScriptedSession
from winagent.api_client import AgentApi
from winagent.events import Event
from winagent.http_client import HttpClient
from winagent.queue import MemoryEventQueue
from winagent.sync import SyncManager

T = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
BASE = "https://api.example.invalid/api/agent/v1"


def _event() -> Event:
    return Event("activity_sample", T, {"state": "active", "idle_seconds": 0.0, "sample_interval_seconds": 30.0})


def test_queue_stays_bounded_under_long_offline_period():
    queue = MemoryEventQueue(5000)
    for _ in range(50_000):  # about 18 hours at one event every 1.3 s
        queue.add(_event())
    assert queue.depth() == 5000
    assert queue.dropped_count() == 45_000


def test_one_day_of_events_flushes_in_bounded_batches_quickly():
    # 8 hours at 30 s sampling, 2 events per sample: about 1,920 events.
    events = [_event() for _ in range(1920)]
    queue = MemoryEventQueue(10_000)
    for e in events:
        queue.add(e)
    responses = []
    for i in range(0, len(events), 500):
        chunk = events[i : i + 500]
        responses.append((200, {"accepted": [e.event_id for e in chunk], "duplicates": [], "rejected": []}))
    session = ScriptedSession(responses)
    mgr = SyncManager(AgentApi(HttpClient(BASE, 5, lambda: "t", session=session)), queue, sleep=lambda s: None)
    start = time.perf_counter()
    result = mgr.flush()
    elapsed = time.perf_counter() - start
    assert result.accepted == 1920
    assert max(len(c["json"]["events"]) for c in session.calls) <= 500
    assert elapsed < 2.0


def test_sampling_cost_per_tick_is_small():
    from tests.fakes import FakeForeground, FakeIdle
    from winagent.monitors.activity_monitor import ActivityMonitor
    from winagent.monitors.app_monitor import AppMonitor
    from winagent.platform_base import ForegroundInfo

    app = AppMonitor(FakeForeground(ForegroundInfo("a.exe", None)), 30)
    act = ActivityMonitor(FakeIdle(1.0), 300, 30)
    start = time.perf_counter()
    for _ in range(10_000):
        app.sample(T)
        act.sample(T)
    per_tick_ms = (time.perf_counter() - start) / 10_000 * 1000
    assert per_tick_ms < 1.0
