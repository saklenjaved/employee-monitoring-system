"""Test helpers that create devices and events directly in the database."""
import uuid
from datetime import datetime, timedelta, timezone

from devices.models import Device
from monitoring.models import MonitoringEvent

UTC = timezone.utc


def at(hour, minute=0, second=0, day=2, month=11):
    """A UTC time on 2026-11-02 (or another day)."""
    return datetime(2026, month, day, hour, minute, second, tzinfo=UTC)


def make_device(user, name="PC"):
    return Device.objects.create(
        organization=user.organization,
        user=user,
        name=name,
        os_version="Windows 11",
        agent_version="0.1.0",
        credential_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        credential_expires_at=datetime.now(UTC) + timedelta(days=30),
    )


def add_event(device, event_type, captured_at, payload):
    return MonitoringEvent.objects.create(
        organization=device.organization,
        user=device.user,
        device=device,
        event_id=uuid.uuid4(),
        event_type=event_type,
        captured_at=captured_at,
        payload=payload,
    )


def app(device, captured_at, name="chrome.exe", seconds=30):
    """An application sample that covers the `seconds` before `captured_at`."""
    return add_event(device, "app_focus", captured_at, {"process_name": name, "duration_seconds": seconds})


def site(device, captured_at, domain="example.com", seconds=30):
    return add_event(device, "website_visit", captured_at, {"domain": domain, "duration_seconds": seconds})


def activity(device, captured_at, state="active", seconds=30):
    return add_event(
        device,
        "activity_sample",
        captured_at,
        {"state": state, "idle_seconds": 0 if state == "active" else 400, "sample_interval_seconds": seconds},
    )
