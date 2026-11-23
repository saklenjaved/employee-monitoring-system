"""End-to-end test (brief: testing, one end-to-end test).

Starts the local mock backend over HTTPS, registers a device with the test code, then runs the
real Agent for a short monitoring window. Windows calls (foreground window, idle time, screen,
session) are fakes so this runs on any OS. Checks that events and one screenshot reach the backend.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.fakes import FakeCapturer, FakeForeground, FakeIdle, FakeProtector, FakeSession
from tests.test_mock_backend import Running, _png, cert_pair  # noqa: F401  (cert_pair fixture)
from winagent.agent_policy import parse_policy
from winagent.api_client import AgentApi
from winagent.config import AgentConfig, ApiConfig, ScreenshotConfig
from winagent.credentials import CredentialStore
from winagent.http_client import HttpClient
from winagent.launcher import Agent
from winagent.monitors.activity_monitor import ActivityMonitor
from winagent.monitors.app_monitor import AppMonitor
from winagent.monitors.website_monitor import StubWebsiteMonitor
from winagent.platform_base import ForegroundInfo
from winagent.queue import MemoryEventQueue
from winagent.registration import register_device
from winagent.screenshots.manager import ScreenshotManager
from winagent.screenshots.redact import SolidFillRedactor
from winagent.screenshots.uploader import PresignedS3Uploader
from winagent.sqlite_queue import SqliteEventQueue
from winagent.sync import SyncManager

DEMO_CODE = "DEMO-LOCAL-0001"


def _window_policy(tmp_path: Path) -> Path:
    """Policy with a short monitoring window around now, and screenshots enabled for this test only."""
    now = datetime.now().astimezone()
    start = (now - timedelta(minutes=5)).strftime("%H:%M")
    end = (now + timedelta(minutes=30)).strftime("%H:%M")
    data = {
        "policy_version": "e2e-1",
        "monitoring": {"enabled": True, "days": [0, 1, 2, 3, 4, 5, 6], "start": start, "end": end},
        "activity": {"sample_interval_seconds": 30, "idle_threshold_seconds": 300},
        "screenshots": {"enabled": True, "interval_seconds": 600},
        "exclusions": {"apps": ["KeePass.exe"], "blur_regions": []},
    }
    path = tmp_path / "e2e_policy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_agent_sends_events_and_screenshot_to_mock_backend(cert_pair, tmp_path):  # noqa: F811
    srv = Running(*cert_pair, data_dir=tmp_path / "backend", policy=_window_policy(tmp_path)).start()
    try:
        data_dir = tmp_path / "agent"
        cfg = AgentConfig(
            api=ApiConfig(srv.base, ca_bundle=str(srv.cert)),
            screenshots=ScreenshotConfig(enabled=True),
            data_dir=data_dir,
        )
        store = CredentialStore(cfg.credential_path, FakeProtector())
        register_device(AgentApi(HttpClient(srv.base, 5, lambda: None, ca_bundle=str(srv.cert))),
                        store, DEMO_CODE, "E2E-PC", "Windows 11", "0.1.0")

        http = HttpClient(srv.base, 5, lambda: store.load().token, ca_bundle=str(srv.cert))
        api = AgentApi(http)
        queue = SqliteEventQueue(cfg.queue_path, FakeProtector(), 10_000, 72 * 3600)
        sync = SyncManager(api, queue)
        foreground = FakeForeground(ForegroundInfo("notepad.exe", "Untitled - Notepad"))
        shots = ScreenshotManager(
            api=api, uploader=PresignedS3Uploader(http), capturer=FakeCapturer(_png()),
            session=FakeSession(), redactor=SolidFillRedactor(), temp_dir=cfg.temp_dir,
            max_bytes=10 * 1024 * 1024,
            foreground_process=lambda: "notepad.exe",
        )
        clock = {"t": 0.0}
        agent = Agent(
            config=cfg, version="0.1.0", api=api, credentials=store, queue=queue, sync=sync,
            app_monitor=AppMonitor(foreground, 30, include_title=True),
            activity_monitor=ActivityMonitor(FakeIdle(5.0), 300, 30),
            website=StubWebsiteMonitor(), screenshots=shots, session=FakeSession(),
            clock=lambda: clock["t"],
        )
        now = datetime.now(timezone.utc)
        # The first cycle only asks the backend for the start; collection begins on the next one.
        for _ in range(4):  # four cycles, 31 s apart
            agent.tick(now)
            clock["t"] += 31
            now += timedelta(seconds=31)

        assert agent.health.monitoring_state == "active"
        assert agent.health.connected is True
        assert queue.depth() == 0  # everything acknowledged by the backend

        import sqlite3

        db = sqlite3.connect(srv.data_dir / "backend.sqlite3")
        kinds = dict(db.execute("SELECT event_type, COUNT(*) FROM events GROUP BY event_type").fetchall())
        assert kinds.get("app_focus", 0) >= 3
        assert kinds.get("activity_sample", 0) >= 3
        shot = db.execute("SELECT status FROM screenshots").fetchall()
        assert shot == [("completed",)]
        stored = list((srv.data_dir / "storage").glob("*.png"))
        assert len(stored) == 1
        assert list(cfg.temp_dir.glob("*.png")) == []  # local copy deleted after upload
        db.close()
        queue.close()
    finally:
        srv.stop()
