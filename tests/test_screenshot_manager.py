"""Screenshot manager tests (TAD 9, TAD 12 'Screenshots'). Every Windows call is faked, and the S3 upload is mocked."""

import io
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from tests.fake_http import ScriptedSession
from tests.fakes import FakeCapturer, FakeSession
from winagent.api_client import AgentApi
from winagent.http_client import HttpClient, PermanentError, TransientError
from winagent.screenshots.manager import ScreenshotManager
from winagent.screenshots.policy import Rect, ScreenshotPolicy
from winagent.screenshots.redact import SolidFillRedactor
from winagent.schedule import MonitoringWindow
from datetime import time

BASE = "https://api.example.invalid/api/agent/v1"
WORKDAY_9AM = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)  # a Monday
WINDOW = MonitoringWindow(days=frozenset(range(7)), start=time(0, 0), end=time(23, 59), enabled=True)


def make_png(w=40, h=30, color=(200, 10, 10)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


class MockUploader:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.puts: list[dict] = []

    def put(self, upload_url, data, headers):
        self.puts.append({"url": upload_url, "data": data, "headers": dict(headers)})
        if self.error:
            raise self.error


def build(tmp_path: Path, outcomes, uploader=None, session=None, capturer=None, foreground=None):
    http_session = ScriptedSession(outcomes)
    api = AgentApi(HttpClient(BASE, 5, lambda: "tok", session=http_session))
    uploader = uploader or MockUploader()
    mgr = ScreenshotManager(
        api=api,
        uploader=uploader,
        capturer=capturer or FakeCapturer(make_png()),
        session=session or FakeSession(),
        redactor=SolidFillRedactor(),
        temp_dir=tmp_path / "tmp",
        max_bytes=10 * 1024 * 1024,
        now=lambda: WORKDAY_9AM,
        foreground_process=lambda: foreground,
    )
    return mgr, http_session, uploader


AUTH_OK = (200, {"screenshot_id": "x", "upload_url": "https://bucket.s3.example.invalid/obj?X-Amz-Signature=SECRET", "upload_method": "PUT", "expires_at": "2026-11-02T09:05:00Z"})
COMPLETE_OK = (200, {"screenshot_id": "x", "status": "uploaded"})


def test_off_by_default_no_capture_no_network(tmp_path):
    capturer = FakeCapturer()
    mgr, http, _ = build(tmp_path, [], capturer=capturer)
    out = mgr.run_once(ScreenshotPolicy(enabled=False, window=WINDOW))
    assert out.status == "skipped" and out.reason == "policy_disabled"
    assert capturer.calls == 0 and http.calls == []


def test_policy_disabled_skips(tmp_path):
    capturer = FakeCapturer()
    mgr, _, _ = build(tmp_path, [], capturer=capturer)
    assert mgr.run_once(ScreenshotPolicy(enabled=False, window=WINDOW)).reason == "policy_disabled"
    assert capturer.calls == 0


def test_outside_hours_skips_before_capture(tmp_path):
    capturer = FakeCapturer()
    closed = MonitoringWindow(days=frozenset(range(7)), start=time(18, 0), end=time(19, 0))
    mgr, _, _ = build(tmp_path, [], capturer=capturer)
    assert mgr.run_once(ScreenshotPolicy(enabled=True, window=closed)).reason == "outside_monitoring_hours"
    assert capturer.calls == 0


def test_locked_session_and_rdp_skip_before_capture(tmp_path):
    capturer = FakeCapturer()
    mgr, _, _ = build(tmp_path, [], capturer=capturer, session=FakeSession(locked=True))
    assert mgr.run_once(ScreenshotPolicy(enabled=True, window=WINDOW)).reason == "session_locked"
    mgr2, _, _ = build(tmp_path, [], capturer=capturer, session=FakeSession(kind="rdp"))
    assert mgr2.run_once(ScreenshotPolicy(enabled=True, window=WINDOW)).reason == "unsupported_session"
    assert capturer.calls == 0


def test_excluded_app_skips_before_capture(tmp_path):
    capturer = FakeCapturer()
    mgr, _, _ = build(tmp_path, [], capturer=capturer, foreground="KeePass.exe")
    policy = ScreenshotPolicy(enabled=True, window=WINDOW, excluded_apps=frozenset({"keepass.exe"}))
    assert mgr.run_once(policy).reason == "excluded_application"
    assert capturer.calls == 0


def test_happy_path_order_and_cleanup(tmp_path):
    uploader = MockUploader()
    mgr, http, _ = build(tmp_path, [AUTH_OK, COMPLETE_OK], uploader=uploader)
    out = mgr.run_once(ScreenshotPolicy(enabled=True, window=WINDOW))
    assert out.status == "uploaded"
    # Authorize first, then the direct upload, then completion.
    assert http.calls[0]["url"].endswith("/screenshots/authorize/")
    assert http.calls[1]["url"].endswith(f"/screenshots/{out.screenshot_id}/complete/")
    assert uploader.puts[0]["url"].startswith("https://bucket.s3")
    assert list((tmp_path / "tmp").glob("*.png")) == []  # local file deleted after upload


def test_authorize_body_has_hash_and_no_org_fields(tmp_path):
    mgr, http, _ = build(tmp_path, [AUTH_OK, COMPLETE_OK])
    mgr.run_once(ScreenshotPolicy(enabled=True, window=WINDOW))
    body = http.json_body_of(0)
    assert len(body["sha256"]) == 64
    assert body["content_type"] == "image/png"
    assert "organization_id" not in body and "employee_id" not in body


def test_upload_failure_reports_failed_and_deletes_local_file(tmp_path):
    uploader = MockUploader(error=TransientError(503, "upload_failed"))
    complete_failed = (200, {"screenshot_id": "x", "status": "failed"})
    mgr, http, _ = build(tmp_path, [AUTH_OK, complete_failed], uploader=uploader)
    out = mgr.run_once(ScreenshotPolicy(enabled=True, window=WINDOW))
    assert out.status == "failed" and out.reason == "upload_failed"
    assert list((tmp_path / "tmp").glob("*.png")) == []
    assert http.json_body_of(1)["status"] == "failed"


def test_rejected_upload_is_failed_not_retried(tmp_path):
    uploader = MockUploader(error=PermanentError(403, "upload_rejected"))
    mgr, http, _ = build(tmp_path, [AUTH_OK, COMPLETE_OK], uploader=uploader)
    assert mgr.run_once(ScreenshotPolicy(enabled=True, window=WINDOW)).status == "failed"
    assert len(uploader.puts) == 1


def test_capture_error_is_contained(tmp_path):
    class Boom:
        def capture_png(self):
            raise OSError("capture unavailable")

    mgr, http, uploader = build(tmp_path, [], capturer=Boom())
    out = mgr.run_once(ScreenshotPolicy(enabled=True, window=WINDOW))
    assert out.reason == "capture_failed"
    assert http.calls == [] and uploader.puts == []


def test_redaction_applied_before_upload(tmp_path):
    png = make_png(40, 30, (255, 255, 255))
    policy = ScreenshotPolicy(enabled=True, window=WINDOW, blur_regions=(Rect(0, 0, 40, 30),))
    uploader = MockUploader()
    mgr, http, _ = build(tmp_path, [AUTH_OK, COMPLETE_OK], uploader=uploader, capturer=FakeCapturer(png))
    mgr.run_once(policy)
    uploaded = Image.open(io.BytesIO(uploader.puts[0]["data"])).convert("RGB")
    assert uploaded.getpixel((5, 5)) == (0, 0, 0)  # whole frame filled
    assert http.json_body_of(0)["redaction_applied"] is True


def test_no_regions_means_no_redaction(tmp_path):
    mgr, http, _ = build(tmp_path, [AUTH_OK, COMPLETE_OK])
    mgr.run_once(ScreenshotPolicy(enabled=True, window=WINDOW))
    assert http.json_body_of(0)["redaction_applied"] is False


def test_auth_error_stops_and_reports_auth_failed(tmp_path):
    mgr, _, uploader = build(tmp_path, [(401, {})])
    out = mgr.run_once(ScreenshotPolicy(enabled=True, window=WINDOW))
    assert out.status == "auth_failed"
    assert uploader.puts == []


def test_presigned_url_never_in_outcome(tmp_path):
    mgr, _, _ = build(tmp_path, [AUTH_OK, COMPLETE_OK])
    out = mgr.run_once(ScreenshotPolicy(enabled=True, window=WINDOW))
    assert "SECRET" not in repr(out)


def test_oversize_image_is_skipped(tmp_path):
    http = ScriptedSession([])
    api = AgentApi(HttpClient(BASE, 5, lambda: "tok", session=http))
    mgr = ScreenshotManager(api, MockUploader(), FakeCapturer(make_png()), FakeSession(), SolidFillRedactor(),
                            tmp_path / "tmp", 10, now=lambda: WORKDAY_9AM)
    assert mgr.run_once(ScreenshotPolicy(enabled=True, window=WINDOW)).reason == "image_too_large"
    assert http.calls == []


def test_policy_denial_403_skips_and_does_not_stop_sync(tmp_path):
    """A 403 from authorize means the policy denies screenshots now. It must not mark the device as unauthorised."""
    denied = (403, {"code": "policy_denies", "detail": "screenshots are disabled"})
    mgr, http, uploader = build(tmp_path, [denied])
    out = mgr.run_once(ScreenshotPolicy(enabled=True, window=WINDOW))
    assert out.status == "skipped" and out.reason == "policy_denied"
    assert uploader.puts == []


def test_bad_credential_401_on_authorize_stops_as_auth_failed(tmp_path):
    mgr, _, uploader = build(tmp_path, [(401, {"code": "invalid_credential"})])
    out = mgr.run_once(ScreenshotPolicy(enabled=True, window=WINDOW))
    assert out.status == "auth_failed"
    assert uploader.puts == []
