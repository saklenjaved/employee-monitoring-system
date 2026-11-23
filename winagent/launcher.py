"""Agent launcher (TAD 5.1, 6). Loads config and credential, enforces monitoring hours, runs the timers.

Each tick:
1. No credential -> status not_registered, nothing collected.
2. Sync stopped by a rejected credential -> status needs_registration, nothing collected.
3. Refresh policy when due. Keep the last good policy if the refresh fails.
4. Session is not a console session (RDP, Citrix, VDI) -> log the session type only, collect nothing (TAD 13 default).
5. Outside monitoring hours -> status paused_hours, nothing collected.
6. Inside hours -> sample app and activity at the configured interval, queue events,
   flush sync, run screenshots (separate flow), send heartbeat.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable

from winagent.agent_policy import AgentPolicy, PolicyError, parse_policy
from winagent.api_client import AgentApi
from winagent.config import AgentConfig
from winagent.credentials import CredentialStore, DeviceCredential
from winagent.events import utc_now
from winagent.health import HealthSnapshot, write_health
from winagent.http_client import AuthError, HttpError
from winagent.monitors.activity_monitor import ActivityMonitor
from winagent.monitors.app_monitor import AppMonitor
from winagent.monitors.website_monitor import StubWebsiteMonitor
from winagent.platform_base import SessionInspector
from winagent.queue import EventQueue
from winagent.registration import _parse_expiry
from winagent.screenshots.manager import ScreenshotManager
from winagent.sync import SyncManager

log = logging.getLogger("winagent.launcher")

# While the employee's start is waiting for this PC, the agent asks the server more often.
AWAITING_START_HEARTBEAT_SECONDS = 60
# States in which the agent holds a start. Only "active" collects anything.
START_FOLLOWING_STATES = ("active", "paused_hours", "unsupported_session")
# The credential is renewed this long before it expires, so the PC never has to ask to connect again.
RENEW_BEFORE = timedelta(days=3)
RENEW_CHECK_SECONDS = 3600


@dataclass
class Timers:
    policy_due: float = 0.0
    sample_due: float = 0.0
    sync_due: float = 0.0
    screenshot_due: float = 0.0
    heartbeat_due: float = 0.0
    renew_due: float = 0.0


class Agent:
    def __init__(
        self,
        config: AgentConfig,
        version: str,
        api: AgentApi,
        credentials: CredentialStore,
        queue: EventQueue,
        sync: SyncManager,
        app_monitor: AppMonitor,
        activity_monitor: ActivityMonitor,
        website: StubWebsiteMonitor,
        screenshots: ScreenshotManager,
        session: SessionInspector,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cfg = config
        self._version = version
        self._api = api
        self._creds = credentials
        self._queue = queue
        self._sync = sync
        self._app = app_monitor
        self._activity = activity_monitor
        self._website = website
        self._shots = screenshots
        self._session = session
        self._clock = clock
        self._timers = Timers()
        self._policy: AgentPolicy | None = None
        self._logged_session: str | None = None
        self._last_skip_reason: str | None = None
        # Collection needs a start from the server. This is the start the agent is running under, and the
        # monotonic time it stops. Zero means no start, so the agent collects nothing.
        self._start_id: str | None = None
        self._authorized_until = 0.0
        self._policy_checked_at = float("-inf")  # when the policy was last read from the server
        self.health = HealthSnapshot(agent_version=version)

    @property
    def policy(self) -> AgentPolicy | None:
        return self._policy

    def tick(self, now_utc: datetime | None = None) -> str:
        now_utc = now_utc or utc_now()
        mono = self._clock()
        state = self._compute_state(now_utc, mono)
        self.health.monitoring_state = state
        self.health.queue_depth = self._queue.depth()
        self.health.dropped_events = self._queue.dropped_count()
        self.health.set_sync_time(self._sync.last_successful_sync_at)
        self.health.last_error_code = self._sync.last_error_code
        self.health.policy_version = self._policy.version if self._policy else None
        self.health.screenshots_enabled = bool(self._policy and self._policy.screenshots.enabled)
        self.health.connected = self._sync.last_error_code is None and self._sync.last_successful_sync_at is not None
        write_health(self._cfg.health_path, self.health)
        return state

    def _compute_state(self, now_utc: datetime, mono: float) -> str:
        if self._creds.load() is None:
            return "not_registered"
        if self._sync.auth_failed:
            return "needs_registration"
        if mono >= self._timers.renew_due:
            self._renew_if_due(now_utc, mono)

        if mono >= self._timers.policy_due:
            self._refresh_policy(mono)
        if self._policy is None:
            return "paused_policy"
        # The employee starts monitoring from the dashboard. Without a current start, collect nothing.
        if mono >= self._authorized_until:
            self._maybe_heartbeat(now_utc, mono, "awaiting_start")
            return "awaiting_start"

        session_type = self._session.session_type()
        self.health.session_type = session_type
        if session_type != "console":
            if self._logged_session != session_type:
                log.info("session type %s is not supported; no monitoring in this session", session_type)
                self._logged_session = session_type
            self._maybe_heartbeat(now_utc, mono, "unsupported_session")
            return "unsupported_session"

        if not self._policy.window.is_open(now_utc.astimezone()):
            self._maybe_heartbeat(now_utc, mono, "paused_hours")
            return "paused_hours"

        if mono >= self._timers.sample_due:
            self._sample(now_utc)
            self._timers.sample_due = mono + self._policy.sample_interval_seconds
        if mono >= self._timers.sync_due:
            self._flush(mono)
        if mono >= self._timers.screenshot_due:
            self._screenshot_pass(mono)
        self._maybe_heartbeat(now_utc, mono, "active")
        return "active"

    @property
    def needs_pairing(self) -> bool:
        """The server rejected this PC's credential. The caller must forget it and ask to connect again."""
        return self._sync.auth_failed

    def _renew_if_due(self, now_utc: datetime, mono: float) -> None:
        self._timers.renew_due = mono + RENEW_CHECK_SECONDS
        cred = self._creds.load()
        if cred is None or cred.expires_at - now_utc > RENEW_BEFORE:
            return
        try:
            reply = self._api.renew_credential()
        except AuthError:
            # Expired or revoked: renewal cannot help, so the PC must connect again.
            self._sync.auth_failed = True
            return
        except HttpError as exc:
            log.warning("credential renewal failed (%s)", exc.for_log())
            return
        self._creds.save(
            DeviceCredential(
                device_id=cred.device_id,
                token=reply["credential"],
                expires_at=_parse_expiry(reply["expires_at"]),
            )
        )
        log.info("credential renewed")

    def _refresh_policy(self, mono: float) -> None:
        self._policy_checked_at = mono
        try:
            raw = self._api.get_config()
            before = self._policy.screenshots if self._policy else None
            parsed = parse_policy(raw, self._cfg.monitoring, self._cfg.screenshots)
            # Local exclusions always apply, and the server can add more.
            apps = frozenset(self._cfg.monitoring.excluded_apps) | parsed.excluded_apps
            self._policy = replace(
                parsed,
                excluded_apps=apps,
                screenshots=replace(parsed.screenshots, excluded_apps=apps),
            )
            self._app.set_excluded(apps)
            log.info("policy refreshed (version %s)", self._policy.version)
            shots = self._policy.screenshots
            if before is None or (before.enabled, before.interval_seconds) != (shots.enabled, shots.interval_seconds):
                log.info("screenshot settings: enabled=%s interval=%ss", shots.enabled, shots.interval_seconds)
        except AuthError:
            self._sync.auth_failed = True
            self._sync.last_error_code = "auth_failed"
        except (HttpError, PolicyError) as exc:
            # Keep the last good policy. Never widen collection on a failed refresh.
            log.warning("policy refresh failed: %s", type(exc).__name__)
        self._timers.policy_due = mono + self._cfg.monitoring.policy_refresh_seconds

    def _sample(self, now_utc: datetime) -> None:
        app_event = self._app.sample(now_utc)
        if app_event is not None and not self._app.is_excluded(app_event.payload.get("process_name")):
            self._sync.enqueue(app_event)
        act = self._activity.sample(now_utc)
        if act is not None:
            self._sync.enqueue(act)
        for ev in self._website.collect(now_utc):
            self._sync.enqueue(ev)

    def _flush(self, mono: float) -> None:
        result = self._sync.flush()
        # The policy's upload interval wins once a policy is loaded. Before that, the local default applies.
        interval = self._policy.sync_interval_seconds if self._policy else self._cfg.monitoring.sync_interval_seconds
        self._timers.sync_due = mono + interval
        if result.auth_failed:
            log.warning("sync stopped: re-registration required")

    def _screenshot_pass(self, mono: float) -> None:
        # Read the Admin's latest setting right before capturing, so turning screenshots OFF stops the next capture.
        # A policy read earlier in this same tick is already fresh, so it is not read twice.
        if mono - self._policy_checked_at > 1.0:
            self._refresh_policy(mono)
        if self._policy is None or self._sync.auth_failed:
            return
        self._timers.screenshot_due = mono + self._policy.screenshots.interval_seconds
        if not self._policy.screenshots.enabled:
            return
        try:
            outcome = self._shots.run_once(self._policy.screenshots)
        except Exception as exc:  # screenshot failures never stop event sync
            log.warning("screenshot pass failed: %s", type(exc).__name__)
            return
        if outcome.status in ("uploaded", "failed", "auth_failed"):
            log.info("screenshot %s (%s)", outcome.status, outcome.reason or "ok")
        elif outcome.reason != self._last_skip_reason:
            # Log a skip only when its reason changes, so the log stays short.
            log.info("screenshot skipped: %s", outcome.reason)
            self._last_skip_reason = outcome.reason
        if outcome.status == "auth_failed":
            self._sync.auth_failed = True

    def _maybe_heartbeat(self, now_utc: datetime, mono: float, state: str) -> None:
        if mono < self._timers.heartbeat_due:
            return
        interval = (
            AWAITING_START_HEARTBEAT_SECONDS if state == "awaiting_start" else self._cfg.monitoring.heartbeat_interval_seconds
        )
        self._timers.heartbeat_due = mono + interval
        try:
            response = self._api.heartbeat(
                agent_version=self._version,
                sent_at=now_utc,
                session_type=self._session.session_type(),
                queue_depth=self._queue.depth(),
                last_successful_sync_at=self._sync.last_successful_sync_at,
                last_error_code=self._sync.last_error_code,
                monitoring_state=state,
                # The start is confirmed once the agent follows it: collecting, or paused by hours or the session type.
                # A start received outside monitoring hours is then kept, and collection begins when the hours open.
                monitoring_start_id=self._start_id if state in START_FOLLOWING_STATES else None,
                focus=self._app.focus_for_heartbeat() if state == "active" else None,
            )
            self._apply_start(response, mono)
        except AuthError:
            self._sync.auth_failed = True
        except HttpError as exc:
            log.warning("heartbeat failed (%s)", exc.for_log())

    def _apply_start(self, response: dict, mono: float) -> None:
        """Take the server's start instruction. No instruction means stop collecting at once."""
        start = response.get("start") if isinstance(response, dict) else None
        if not start:
            self._start_id = None
            self._authorized_until = 0.0
            return
        if start.get("id") != self._start_id:
            self._start_id = start.get("id")
            # Confirm to the server at the next tick rather than waiting for the normal heartbeat.
            self._timers.heartbeat_due = mono
            log.info("monitoring start received")
        self._authorized_until = mono + int(start.get("authorized_seconds", 0))

    def run_forever(self, stop: threading.Event, tick_seconds: float = 1.0) -> None:
        log.info("agent started (version %s)", self._version)
        while not stop.is_set() and not self.needs_pairing:
            try:
                self.tick()
            except Exception:  # keep the agent alive; log only the exception type
                log.exception("tick failed")
            stop.wait(tick_seconds)
        log.info("agent stopped")
