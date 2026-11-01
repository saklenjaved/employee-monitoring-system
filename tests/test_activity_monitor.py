from datetime import datetime, timezone

from tests.fakes import FakeIdle
from winagent.monitors.activity_monitor import ActivityMonitor
from winagent.monitors.website_monitor import StubWebsiteMonitor

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


def test_active_below_threshold():
    ev = ActivityMonitor(FakeIdle(10.0), 300, 30).sample(NOW)
    assert ev.payload["state"] == "active"
    assert ev.payload["idle_seconds"] == 10.0
    assert ev.payload["sample_interval_seconds"] == 30.0


def test_idle_at_and_above_threshold():
    mon = ActivityMonitor(FakeIdle(300.0), 300, 30)
    assert mon.sample(NOW).payload["state"] == "idle"
    assert ActivityMonitor(FakeIdle(999.0), 300, 30).sample(NOW).payload["state"] == "idle"


def test_threshold_is_configurable():
    assert ActivityMonitor(FakeIdle(120.0), 120, 30).classify(120.0) == "idle"
    assert ActivityMonitor(FakeIdle(119.0), 120, 30).classify(119.0) == "active"


def test_negative_idle_clamped_to_zero():
    assert ActivityMonitor(FakeIdle(-5.0), 300, 30).sample(NOW).payload["idle_seconds"] == 0.0


def test_read_failure_skips_sample():
    assert ActivityMonitor(FakeIdle(raise_error=OSError("no")), 300, 30).sample(NOW) is None


def test_payload_has_only_allowed_keys():
    payload = ActivityMonitor(FakeIdle(1.0), 300, 30).sample(NOW).payload
    assert set(payload) == {"state", "idle_seconds", "sample_interval_seconds"}


def test_website_stub_emits_nothing_and_is_unsupported():
    stub = StubWebsiteMonitor()
    assert stub.supported is False
    assert stub.collect(NOW) == []
    assert stub.collect(NOW) == []
