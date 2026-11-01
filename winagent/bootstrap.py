"""Wires the real Windows adapters into the Agent. The only place that knows about concrete classes."""

from __future__ import annotations

import sys
from typing import Callable

from winagent.api_client import AgentApi
from winagent.config import AgentConfig
from winagent.credentials import CredentialStore
from winagent.http_client import HttpClient
from winagent.launcher import Agent
from winagent.monitors.activity_monitor import ActivityMonitor
from winagent.monitors.app_monitor import AppMonitor
from winagent.monitors.website_monitor import StubWebsiteMonitor
from winagent.screenshots.manager import ScreenshotManager
from winagent.screenshots.redact import SolidFillRedactor
from winagent.screenshots.uploader import PresignedS3Uploader
from winagent.sqlite_queue import SqliteEventQueue
from winagent.sync import SyncManager


def build_agent(cfg: AgentConfig, version: str) -> tuple[Agent, Callable | None]:
    if sys.platform != "win32":
        raise RuntimeError("the agent runs on Windows only; use the fakes in tests/ elsewhere")
    from winagent import platform_win as win

    protector = win.DpapiProtector()
    store = CredentialStore(cfg.credential_path, protector)

    def token() -> str | None:
        cred = store.load()
        return cred.token if cred else None

    http = HttpClient(cfg.api.base_url, cfg.api.timeout_seconds, token, ca_bundle=cfg.api.ca_bundle)
    api = AgentApi(http)
    queue = SqliteEventQueue(
        cfg.queue_path,
        protector,
        max_events=cfg.monitoring.queue_max_events,
        max_age_seconds=cfg.monitoring.queue_max_age_hours * 3600,
    )
    sync = SyncManager(api, queue, batch_size=cfg.monitoring.batch_size)

    foreground = win.Win32ForegroundWindowReader()
    app = AppMonitor(
        foreground,
        cfg.monitoring.sample_interval_seconds,
        include_title=cfg.monitoring.capture_window_title,
        excluded_apps=frozenset(cfg.monitoring.excluded_apps),
        title_keywords_excluded=cfg.monitoring.title_keywords_excluded,
    )
    activity = ActivityMonitor(win.Win32IdleTimeReader(), cfg.monitoring.idle_threshold_seconds, cfg.monitoring.sample_interval_seconds)
    session = win.Win32SessionInspector()

    def foreground_name() -> str | None:
        info = foreground.read(include_title=False)
        return info.process_name if info else None

    shots = ScreenshotManager(
        api=api,
        uploader=PresignedS3Uploader(http),
        capturer=win.MssScreenCapturer(),
        session=session,
        redactor=SolidFillRedactor(),
        temp_dir=cfg.temp_dir,
        local_enabled=cfg.screenshots.enabled,
        max_bytes=cfg.screenshots.max_upload_bytes,
        foreground_process=foreground_name,
    )

    agent = Agent(
        config=cfg,
        version=version,
        api=api,
        credentials=store,
        queue=queue,
        sync=sync,
        app_monitor=app,
        activity_monitor=activity,
        website=StubWebsiteMonitor(),
        screenshots=shots,
        session=session,
    )

    from winagent.tray import start_tray

    return agent, start_tray
