"""Screenshot manager (TAD 9). Order is fixed; stop at the first failed check.

1. Policy checks (enabled, hours, session, lock, excluded app)   -> screenshots/policy.py
2. Capture PNG in memory                                        -> platform_base.ScreenCapturer
3. Redact policy regions before anything touches disk           -> screenshots/redact.py
4. Write to a private temp file and compute SHA-256
5. Request upload authorization (server re-checks policy)       -> api_client.authorize_screenshot
6. Upload directly to S3 with that URL                          -> screenshots/uploader.py
7. Report completion                                            -> api_client.complete_screenshot
8. Always delete the local file in finally                      (screenshot-policy rule)

Failures here never block event sync; the launcher runs this on its own timer.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from winagent.api_client import AgentApi
from winagent.http_client import AuthError, HttpError
from winagent.platform_base import ScreenCapturer, SessionInspector
from winagent.screenshots.policy import ScreenshotPolicy
from winagent.screenshots.redact import SolidFillRedactor
from winagent.screenshots.uploader import Uploader

log = logging.getLogger("winagent.screenshots")


@dataclass(frozen=True)
class ScreenshotOutcome:
    status: str  # "skipped", "uploaded", "failed", "auth_failed"
    reason: str | None = None
    screenshot_id: str | None = None


class ScreenshotManager:
    def __init__(
        self,
        api: AgentApi,
        uploader: Uploader,
        capturer: ScreenCapturer,
        session: SessionInspector,
        redactor: SolidFillRedactor,
        temp_dir: Path,
        max_bytes: int,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        foreground_process: Callable[[], str | None] = lambda: None,
    ) -> None:
        self._api = api
        self._uploader = uploader
        self._capturer = capturer
        self._session = session
        self._redactor = redactor
        self._temp = Path(temp_dir)
        self._max_bytes = max_bytes
        self._now = now
        self._foreground = foreground_process

    def run_once(self, policy: ScreenshotPolicy) -> ScreenshotOutcome:
        now = self._now()
        reason = policy.skip_reason(
            local_now=now.astimezone(),
            session_type=self._session.session_type(),
            locked=self._session.is_locked(),
            foreground_process=self._foreground(),
        )
        if reason is not None:
            return ScreenshotOutcome("skipped", reason)

        try:
            raw = self._capturer.capture_png()
            shot = self._redactor.apply(raw, policy.blur_regions)
        except Exception as exc:  # capture or redaction failed; never upload unredacted data
            log.warning("capture failed: %s", type(exc).__name__)
            return ScreenshotOutcome("failed", "capture_failed")
        del raw

        if len(shot.png) > self._max_bytes:
            return ScreenshotOutcome("skipped", "image_too_large")

        screenshot_id = str(uuid.uuid4())
        path = self._write_temp(shot.png)
        try:
            digest = hashlib.sha256(shot.png).hexdigest()
            auth = self._api.authorize_screenshot(
                {
                    "screenshot_id": screenshot_id,
                    "captured_at": now.isoformat().replace("+00:00", "Z"),
                    "content_type": "image/png",
                    "byte_size": len(shot.png),
                    "sha256": digest,
                    "width": shot.width,
                    "height": shot.height,
                    "redaction_applied": shot.redaction_applied,
                }
            )
            upload_url = auth["upload_url"]
            headers = dict(auth.get("upload_headers") or {})
            headers.setdefault("Content-Type", "image/png")
            data = path.read_bytes()
            try:
                self._uploader.put(upload_url, data, headers)
            except HttpError as exc:
                self._safe_complete(screenshot_id, "failed", exc.code)
                return ScreenshotOutcome("failed", exc.code, screenshot_id)
            self._safe_complete(screenshot_id, "uploaded")
            return ScreenshotOutcome("uploaded", None, screenshot_id)
        except AuthError as exc:
            # 401 means the credential is bad. 403 from authorize means the policy denies screenshots now,
            # and that must not stop event sync.
            if exc.status == 401:
                return ScreenshotOutcome("auth_failed", "auth_failed", screenshot_id)
            return ScreenshotOutcome("skipped", "policy_denied", screenshot_id)
        except HttpError as exc:
            log.warning("screenshot authorization failed (%s)", exc.for_log())
            return ScreenshotOutcome("failed", exc.code, screenshot_id)
        finally:
            self._delete(path)

    def _safe_complete(self, screenshot_id: str, status: str, error_code: str | None = None) -> None:
        try:
            self._api.complete_screenshot(screenshot_id, status, error_code)
        except HttpError as exc:
            log.warning("screenshot completion report failed (%s)", exc.for_log())

    def _write_temp(self, data: bytes) -> Path:
        self._temp.mkdir(parents=True, exist_ok=True)
        path = self._temp / f"{secrets.token_hex(12)}.png"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return path

    @staticmethod
    def _delete(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
