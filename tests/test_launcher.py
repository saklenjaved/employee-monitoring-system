"""Launcher and monitoring-hours tests (TAD 6, TAD 12 'Monitoring'). All Windows calls are fakes."""

import logging
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from tests.fake_http import ScriptedSession
from tests.fakes import FakeAutostart, FakeForeground, FakeIdle, FakeProtector, FakeSession, FakeCapturer
from winagent.agent_policy import parse_policy
from winagent.api_client import AgentApi
from winagent.config import ApiConfig, AgentConfig, MonitoringConfig, ScreenshotConfig
from winagent.credentials import CredentialStore, DeviceCredential
from winagent.health import read_health
from winagent.http_client import HttpClient
from winagent.launcher import Agent
from winagent.monitors.activity_monitor import ActivityMonitor
from winagent.monitors.app_monitor import AppMonitor
from winagent.monitors.website_monitor import StubWebsiteMonitor
from winagent.platform_base import ForegroundInfo
from winagent.queue import MemoryEventQueue
from winagent.schedule import MonitoringWindow
from winagent.screenshots.manager import ScreenshotManager
from winagent.screenshots.redact import SolidFillRedactor
from winagent.sync import SyncManager
from tests.test_screenshot_manager import MockUploader

BASE = "https://api.example.invalid/api/agent/v1"
# Monday 2026-11-02 at 10:00 local. The tests use the device's local time zone, so build aware datetimes from local clock.
TEN_AM = datetime(2026, 11, 2, 10, 0).astimezone()
SIX_PM = datetime(2026, 11, 2, 18, 30).astimezone()

POLICY_JSON = {
    "policy_version": "p1",
    "monitoring": {"enabled": True, "days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"},
    "activity": {"sample_interval_seconds": 30, "idle_threshold_seconds": 300},
    "screenshots": {"enabled": False},
    "exclusions": {"apps": ["keepass.exe"]},
}


class Harness:
    def __init__(self, tmp_path: Path, outcomes, session=None, foreground=None, registered=True, authorized=True):
        self.cfg = AgentConfig(api=ApiConfig(BASE), data_dir=tmp_path)
        self.http = ScriptedSession(outcomes)
        self.api = AgentApi(HttpClient(BASE, 5, lambda: "tok" if registered else None, session=self.http))
        self.store = CredentialStore(self.cfg.credential_path, FakeProtector())
        if registered:
            self.store.save(DeviceCredential("6f1c2a1e-9b7d-4c1e-8a7e-2f3b4c5d6e7f", "tok", datetime(2027, 1, 1, tzinfo=timezone.utc)))
        self.queue = MemoryEventQueue(1000)
        self.sync = SyncManager(self.api, self.queue, sleep=lambda s: None)
        fg = foreground or FakeForeground(ForegroundInfo("chrome.exe", None))
        self.app = AppMonitor(fg, 30)
        self.session = session or FakeSession()
        self.shots = ScreenshotManager(self.api, MockUploader(), FakeCapturer(), self.session, SolidFillRedactor(),
                                       tmp_path / "tmp", False, 10_000_000)
        self.clock_value = 0.0
        self.agent = Agent(
            config=self.cfg, version="0.1.0", api=self.api, credentials=self.store, queue=self.queue, sync=self.sync,
            app_monitor=self.app, activity_monitor=ActivityMonitor(FakeIdle(5.0), 300, 30),
            website=StubWebsiteMonitor(), screenshots=self.shots, session=self.session,
            clock=lambda: self.clock_value,
        )
        if authorized:
            # Most tests check collection itself. Tests of the start gate pass authorized=False.
            self.agent._authorized_until = float("inf")


def test_not_registered_collects_nothing(tmp_path):
    h = Harness(tmp_path, [], registered=False)
    assert h.agent.tick(TEN_AM) == "not_registered"
    assert h.queue.depth() == 0 and h.http.calls == []


def test_inside_hours_samples_and_flushes(tmp_path):
    h = Harness(tmp_path, [
        (200, POLICY_JSON),
        (200, {"accepted": [], "duplicates": [], "rejected": []}),  # flush (acks nothing; batch stays queued)
        (200, {}),  # heartbeat
    ])
    assert h.agent.tick(TEN_AM) == "active"
    assert h.queue.depth() == 2  # app_focus + activity_sample queued
    assert h.agent.health.monitoring_state == "active"


def test_outside_hours_collects_nothing(tmp_path):
    h = Harness(tmp_path, [(200, POLICY_JSON), (200, {})])
    assert h.agent.tick(SIX_PM) == "paused_hours"
    assert h.queue.depth() == 0


def test_weekend_is_outside_hours(tmp_path):
    saturday = (TEN_AM + timedelta(days=5))
    h = Harness(tmp_path, [(200, POLICY_JSON), (200, {})])
    assert h.agent.tick(saturday) == "paused_hours"
    assert h.queue.depth() == 0


def test_rdp_session_collects_nothing_and_logs_type_only(tmp_path, caplog):
    h = Harness(tmp_path, [(200, POLICY_JSON), (200, {})], session=FakeSession(kind="rdp"))
    with caplog.at_level(logging.INFO, logger="winagent"):
        assert h.agent.tick(TEN_AM) == "unsupported_session"
        h.agent.tick(TEN_AM)
    assert h.queue.depth() == 0
    assert "rdp" in caplog.text


def test_excluded_app_is_not_queued_but_activity_is(tmp_path):
    fg = FakeForeground(ForegroundInfo("KeePass.exe", None))
    h = Harness(tmp_path, [(200, POLICY_JSON), (200, {"accepted": [], "duplicates": [], "rejected": []}), (200, {})], foreground=fg)
    h.agent.tick(TEN_AM)
    kinds = [e.event_type for e in h.queue.peek(10)]
    assert kinds == ["activity_sample"]


def test_failed_policy_refresh_keeps_last_good_policy(tmp_path):
    empty_ack = (200, {"accepted": [], "duplicates": [], "rejected": []})
    # Tick 1: policy, flush, heartbeat. Tick 2: policy fails with 503, then flush and heartbeat.
    h = Harness(tmp_path, [(200, POLICY_JSON), empty_ack, (200, {}), (503, {}), empty_ack, (200, {})])
    h.agent.tick(TEN_AM)
    assert h.agent.policy.version == "p1"
    h.clock_value = 10_000  # policy refresh and every other timer are due again
    h.agent.tick(TEN_AM)
    assert h.agent.policy.version == "p1"


def test_auth_failure_stops_collection_until_reregistration(tmp_path):
    h = Harness(tmp_path, [(200, POLICY_JSON), (401, {}), (200, {})])
    h.agent.tick(TEN_AM)  # policy ok, flush gets 401 only if events queued, so force auth failure directly
    h.sync.auth_failed = True
    assert h.agent.tick(TEN_AM) == "needs_registration"


def test_health_file_has_no_secrets(tmp_path):
    h = Harness(tmp_path, [(200, POLICY_JSON), (200, {"accepted": [], "duplicates": [], "rejected": []}), (200, {})])
    h.agent.tick(TEN_AM)
    text = (tmp_path / "health.json").read_text(encoding="utf-8")
    assert "tok" not in text.replace("token", "")
    snap = read_health(tmp_path / "health.json")
    assert snap.monitoring_state == "active" and snap.policy_version == "p1"


def test_policy_parse_clamps_to_bounds():
    bad = dict(POLICY_JSON, activity={"sample_interval_seconds": 1})
    try:
        parse_policy(bad, MonitoringConfig(), ScreenshotConfig())
    except Exception as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("expected PolicyError")


def test_policy_screenshot_flag_from_server():
    raw = dict(POLICY_JSON, screenshots={"enabled": True, "interval_seconds": 600})
    pol = parse_policy(raw, MonitoringConfig(), ScreenshotConfig())
    assert pol.screenshots.enabled is True
    assert pol.screenshots.interval_seconds == 600


def test_window_crosses_midnight():
    night = MonitoringWindow(days=frozenset(range(7)), start=time(22, 0), end=time(6, 0))
    assert night.is_open(datetime(2026, 11, 2, 23, 0))
    assert night.is_open(datetime(2026, 11, 2, 5, 0))
    assert not night.is_open(datetime(2026, 11, 2, 12, 0))


def test_window_disabled_is_always_closed():
    off = MonitoringWindow(days=frozenset(range(7)), start=time(0, 0), end=time(23, 59), enabled=False)
    assert not off.is_open(datetime(2026, 11, 2, 12, 0))


def test_window_uses_the_policy_time_zone_not_the_pc_clock():
    # 08:01 to 00:00 in UTC, as an organization can set it. 20:32 UTC is inside, whatever zone the PC is in.
    utc_window = MonitoringWindow(days=frozenset(range(7)), start=time(8, 1), end=time(0, 0), timezone="UTC")
    moment = datetime(2026, 11, 22, 20, 32, tzinfo=timezone.utc)
    india = moment.astimezone(timezone(timedelta(hours=5, minutes=30)))  # 02:02 the next day on a PC in India
    assert utc_window.is_open(india)
    assert utc_window.is_open(moment)
    assert not utc_window.is_open(datetime(2026, 11, 22, 3, 0, tzinfo=timezone.utc))  # 08:30 in India, closed in UTC


def test_window_day_is_the_policy_zones_day():
    # Monday to Friday 09:00-17:00 in Kolkata. Friday 23:00 UTC is already Saturday 04:30 there: closed.
    kolkata = MonitoringWindow(days=frozenset(range(5)), start=time(9, 0), end=time(17, 0), timezone="Asia/Kolkata")
    assert kolkata.is_open(datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc))  # Monday 09:30 in Kolkata
    assert not kolkata.is_open(datetime(2026, 11, 6, 23, 0, tzinfo=timezone.utc))


def test_window_follows_daylight_saving_in_the_policy_zone():
    london = MonitoringWindow(days=frozenset(range(7)), start=time(9, 0), end=time(17, 0), timezone="Europe/London")
    assert london.is_open(datetime(2026, 7, 1, 8, 30, tzinfo=timezone.utc))  # 09:30 summer time
    assert not london.is_open(datetime(2026, 1, 7, 8, 30, tzinfo=timezone.utc))  # 08:30 winter time


def test_window_without_a_known_zone_keeps_the_device_time():
    for zone in ("", "Not/AZone"):
        window = MonitoringWindow(days=frozenset(range(7)), start=time(9, 0), end=time(17, 0), timezone=zone)
        assert window.is_open(datetime(2026, 11, 2, 10, 0, tzinfo=timezone(timedelta(hours=3))))
        assert not window.is_open(datetime(2026, 11, 2, 18, 0, tzinfo=timezone(timedelta(hours=3))))


def test_policy_time_zone_is_read_from_the_server_policy():
    raw = dict(POLICY_JSON, monitoring=dict(POLICY_JSON["monitoring"], timezone="Asia/Kolkata"))
    pol = parse_policy(raw, MonitoringConfig(), ScreenshotConfig())
    assert pol.window.timezone == "Asia/Kolkata"
    assert pol.screenshots.window.timezone == "Asia/Kolkata"  # screenshots use the same hours


def test_agent_collects_when_the_policy_zone_is_open_even_if_the_pc_clock_is_not(tmp_path):
    # The bug this guards: hours in UTC, PC in another zone. The agent paused while the server allowed capture.
    utc_policy = dict(
        POLICY_JSON, monitoring={"enabled": True, "days": list(range(7)), "start": "08:01", "end": "00:00", "timezone": "UTC"}
    )
    h = Harness(tmp_path, [(200, utc_policy), (200, {"accepted": [], "duplicates": [], "rejected": []}), (200, {})])
    assert h.agent.tick(datetime(2026, 11, 22, 20, 32, tzinfo=timezone.utc)) == "active"
    h2 = Harness(tmp_path / "second", [(200, utc_policy), (200, {})])
    assert h2.agent.tick(datetime(2026, 11, 22, 3, 0, tzinfo=timezone.utc)) == "paused_hours"


def test_autostart_is_per_user_and_reversible():
    reg = FakeAutostart()
    reg.enable('"python" -m winagent run')
    assert reg.is_enabled()
    reg.disable()
    assert not reg.is_enabled()


# --- Start from the dashboard -------------------------------------------------------------------
# The server decides when collection may run. Without a start, the agent collects nothing and asks often.

START = {"id": "3f0c9a8e-1b2d-4c3e-9f4a-5b6c7d8e9f01", "authorized_seconds": 3600}


def test_no_start_collects_nothing_and_asks_the_server_often(tmp_path):
    h = Harness(tmp_path, [(200, POLICY_JSON), (200, {"start": None})], authorized=False)
    assert h.agent.tick(TEN_AM) == "awaiting_start"
    assert h.queue.depth() == 0
    assert h.http.calls[-1]["json"]["monitoring_state"] == "awaiting_start"
    assert "monitoring_start_id" not in h.http.calls[-1]["json"]


def test_start_from_server_collects_and_reports_the_start_id(tmp_path):
    h = Harness(tmp_path, [
        (200, POLICY_JSON),                                        # tick 1: policy
        (200, {"start": START}),                                   # tick 1: waiting heartbeat receives the start
        (200, {"accepted": [], "duplicates": [], "rejected": []}),  # tick 2: flush
        (200, {"start": START}),                                   # tick 2: heartbeat confirms the start
    ], authorized=False)
    assert h.agent.tick(TEN_AM) == "awaiting_start"
    assert h.agent.tick(TEN_AM) == "active"
    assert h.queue.depth() == 2
    assert h.http.calls[-1]["json"]["monitoring_start_id"] == START["id"]


def test_server_without_a_start_stops_collection(tmp_path):
    empty_ack = (200, {"accepted": [], "duplicates": [], "rejected": []})
    h = Harness(tmp_path, [(200, POLICY_JSON), empty_ack, (200, {"start": None}), (200, {"start": None})])
    assert h.agent.tick(TEN_AM) == "active"
    h.clock_value = 10
    assert h.agent.tick(TEN_AM) == "awaiting_start"


def test_a_screenshot_setting_from_the_server_is_logged_when_it_is_applied(tmp_path, caplog):
    h = Harness(tmp_path, [(200, POLICY_JSON), (200, {"start": START})], authorized=False)
    with caplog.at_level(logging.INFO, logger="winagent.launcher"):
        h.agent.tick(TEN_AM)
    assert "screenshot settings: enabled=" in caplog.text


def test_policy_refresh_is_short_enough_for_admin_changes_to_reach_the_pc():
    """Turning screenshots on in the dashboard must reach the PC within a few minutes, not an hour."""
    assert MonitoringConfig().policy_refresh_seconds <= 300


def test_start_runs_out_by_the_agent_clock_when_the_server_cannot_be_reached(tmp_path):
    short = {"id": START["id"], "authorized_seconds": 600}
    h = Harness(tmp_path, [(200, POLICY_JSON), (200, {"start": short}), (200, POLICY_JSON), (503, {})], authorized=False)
    assert h.agent.tick(TEN_AM) == "awaiting_start"
    h.clock_value = 601  # past the authorized time, and the next heartbeat fails (policy refresh is due by now)
    assert h.agent.tick(TEN_AM) == "awaiting_start"
    assert h.queue.depth() == 0


def test_a_start_received_outside_hours_is_confirmed_as_paused_and_collects_nothing(tmp_path):
    """Admin starts monitoring after hours: the agent keeps the start (so it does not run out) but collects nothing."""
    h = Harness(tmp_path, [
        (200, POLICY_JSON),            # tick 1: policy
        (200, {"start": START}),       # tick 1: waiting heartbeat receives the start
        (200, {"start": START}),       # tick 2: paused heartbeat confirms the start
    ], authorized=False)
    assert h.agent.tick(SIX_PM) == "awaiting_start"
    assert h.agent.tick(SIX_PM) == "paused_hours"
    beat = h.http.calls[-1]["json"]
    assert beat["monitoring_state"] == "paused_hours"
    assert beat["monitoring_start_id"] == START["id"]
    assert h.queue.depth() == 0  # nothing collected outside hours


def test_a_kept_start_begins_collecting_when_the_hours_open(tmp_path):
    empty_ack = (200, {"accepted": [], "duplicates": [], "rejected": []})
    h = Harness(tmp_path, [
        (200, POLICY_JSON), (200, {"start": START}),  # tick 1 (after hours): receive the start
        (200, {"start": START}),                      # tick 2 (after hours): confirm while paused
        (200, POLICY_JSON),                           # tick 3: the 5-minute policy refresh is due
        empty_ack, (200, {"start": START}),           # tick 3 (next morning): collect, flush, heartbeat
    ], authorized=False)
    h.agent.tick(SIX_PM)
    h.agent.tick(SIX_PM)
    h.clock_value = 400  # the heartbeat and sample timers are due again; the start is still authorized
    assert h.agent.tick(TEN_AM + timedelta(days=1)) == "active"
    assert h.queue.depth() == 2
    assert h.http.calls[-1]["json"]["monitoring_start_id"] == START["id"]
