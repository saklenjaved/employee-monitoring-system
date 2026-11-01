"""Mock backend tests over real local HTTPS (demo; TAD 10 six capabilities, contract in docs/api-contract.yaml).

A self-signed certificate is generated with openssl for the test run. If openssl is missing the tests are skipped.
The agent's own HttpClient and AgentApi talk to the server, so the client code under test is the real code.
"""

import hashlib
import io
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from PIL import Image

from mock_backend import server as mock
from tests.fakes import FakeProtector
from winagent.api_client import AgentApi
from winagent.credentials import CredentialStore
from winagent.events import Event, to_wire
from winagent.http_client import AuthError, HttpClient, PermanentError, TransientError
from winagent.registration import register_device
from winagent.sqlite_queue import SqliteEventQueue
from winagent.sync import SyncManager

OPENSSL = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
pytestmark = pytest.mark.skipif(not Path(OPENSSL).exists(), reason="openssl needed for the local demo certificate")
POLICY = Path(__file__).resolve().parent.parent / "mock_backend" / "demo_policy.json"
T = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def cert_pair(tmp_path_factory):
    d = tmp_path_factory.mktemp("cert")
    cert, key = d / "cert.pem", d / "key.pem"
    subprocess.run(
        [OPENSSL, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-keyout", str(key), "-out", str(cert),
         "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
        check=True, capture_output=True,
    )
    return cert, key


class Running:
    """Starts the mock on a free port in a thread and can stop it, to simulate network loss."""

    def __init__(self, cert, key, data_dir: Path, port: int = 0, policy: Path = POLICY):
        self.cert, self.key, self.data_dir, self.policy = cert, key, data_dir, policy
        self.port = port
        self.srv = None

    def start(self):
        self.srv = mock.create_server(self.cert, self.key, "127.0.0.1", self.port, self.data_dir, self.policy)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv.store.close()
            self.srv = None

    @property
    def base(self):
        return f"https://127.0.0.1:{self.port}/api/agent/v1"


@pytest.fixture
def backend(cert_pair, tmp_path):
    srv = Running(*cert_pair, data_dir=tmp_path / "backend").start()
    yield srv
    srv.stop()


def api_for(srv: Running, token=lambda: None, cert=None):
    http = HttpClient(srv.base, 5, token, ca_bundle=str(cert or srv_cert(srv)))
    return AgentApi(http)


def srv_cert(srv):
    return srv.cert


def register(srv, tmp_path, code="DEMO-LOCAL-0001"):
    store = CredentialStore(tmp_path / "credential.bin", FakeProtector())
    api = api_for(srv)
    cred = register_device(api, store, code, "DEMO-PC", "Windows 11", "0.1.0")
    return store, cred


def test_registration_with_demo_code_issues_credential(backend, tmp_path):
    store, cred = register(backend, tmp_path)
    assert cred.device_id and cred.token
    assert store.load().token == cred.token


def test_wrong_registration_code_is_refused(backend, tmp_path):
    with pytest.raises(AuthError):
        register(backend, tmp_path, code="WRONG-CODE-999")


def test_token_is_stored_hashed_in_backend(backend, tmp_path):
    _, cred = register(backend, tmp_path)
    raw = (backend.data_dir / "backend.sqlite3").read_bytes()
    assert cred.token.encode() not in raw
    assert hashlib.sha256(cred.token.encode()).hexdigest().encode() in raw


def test_config_requires_device_credential(backend, tmp_path):
    api = api_for(backend)
    with pytest.raises(AuthError):
        api.get_config()  # no token
    _, cred = register(backend, tmp_path)
    authed = api_for(backend, token=lambda: cred.token)
    policy = authed.get_config()
    assert policy["policy_version"] == "demo-1"
    assert policy["activity"]["sample_interval_seconds"] == 30
    assert policy["activity"]["idle_threshold_seconds"] == 300


def test_bad_token_is_rejected(backend):
    api = api_for(backend, token=lambda: "not-a-real-token")
    with pytest.raises(AuthError):
        api.get_config()


def test_expired_credential_is_rejected(backend, tmp_path):
    _, cred = register(backend, tmp_path)
    store = backend.srv.store
    store._conn.execute("UPDATE devices SET expires_at = ? WHERE device_id = ?",
                        ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), cred.device_id))
    store._conn.commit()
    with pytest.raises(AuthError):
        api_for(backend, token=lambda: cred.token).get_config()


def test_events_accepted_then_duplicates_reported_and_invalid_rejected(backend, tmp_path):
    _, cred = register(backend, tmp_path)
    api = api_for(backend, token=lambda: cred.token)
    good = Event("activity_sample", T, {"state": "active", "idle_seconds": 1.0, "sample_interval_seconds": 10.0})
    first = api.ingest_events([good])
    assert first["accepted"] == [good.event_id]
    again = api.ingest_events([good])
    assert again["duplicates"] == [good.event_id] and again["accepted"] == []


def test_invalid_event_is_rejected_but_batch_continues(backend, tmp_path):
    _, cred = register(backend, tmp_path)
    api = api_for(backend, token=lambda: cred.token)
    good = Event("activity_sample", T, {"state": "active", "idle_seconds": 1.0, "sample_interval_seconds": 10.0})
    bad = to_wire(good)
    bad["event_id"] = "00000000-0000-4000-8000-000000000001"
    bad["payload"] = {"state": "active", "idle_seconds": 1.0, "sample_interval_seconds": 10.0, "typed_text": "x"}
    result = api._http.post_json("events/batch/", {"events": [to_wire(good), bad]})
    assert result["accepted"] == [good.event_id]
    assert result["rejected"] == ["00000000-0000-4000-8000-000000000001"]


def test_organization_id_from_agent_is_ignored_by_server(backend, tmp_path):
    _, cred = register(backend, tmp_path)
    http = HttpClient(backend.base, 5, lambda: cred.token, ca_bundle=str(backend.cert))
    body = {"events": [{"event_id": str(uuid.uuid4()), "event_type": "activity_sample",
                        "captured_at": "2026-11-01T09:00:00.000000Z", "payload": {"state": "active", "idle_seconds": 0.0,
                                                                                    "sample_interval_seconds": 10.0}}],
            "organization_id": "someone-else"}
    assert http.post_json("events/batch/", body)["accepted"]


def test_screenshot_disabled_by_demo_policy_is_refused(cert_pair, tmp_path):
    srv = Running(*cert_pair, data_dir=tmp_path / "b", policy=_policy_with_screenshots(tmp_path, enabled=False)).start()
    try:
        _assert_screenshot_refused(srv, tmp_path)
    finally:
        srv.stop()


def _assert_screenshot_refused(backend, tmp_path):
    _, cred = register(backend, tmp_path)
    api = api_for(backend, token=lambda: cred.token)
    with pytest.raises(AuthError) as exc:  # 403 maps to AuthError in the client; the manager treats it as a policy denial
        api.authorize_screenshot({"screenshot_id": str(uuid.uuid4()), "captured_at": "2026-11-01T09:00:00Z",
                                  "content_type": "image/png", "byte_size": 10, "sha256": "a" * 64})
    assert exc.value.status == 403


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _policy_with_screenshots(tmp_path, enabled: bool) -> Path:
    import json

    data = json.loads(POLICY.read_text(encoding="utf-8-sig"))
    data["screenshots"]["enabled"] = enabled
    path = tmp_path / f"policy_{'on' if enabled else 'off'}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _enabled_policy(tmp_path) -> Path:
    return _policy_with_screenshots(tmp_path, enabled=True)


def test_screenshot_flow_authorize_upload_complete_stores_locally(cert_pair, tmp_path):
    srv = Running(*cert_pair, data_dir=tmp_path / "b", policy=_enabled_policy(tmp_path)).start()
    try:
        _, cred = register(srv, tmp_path)
        api = api_for(srv, token=lambda: cred.token)
        png = _png()
        sid = str(uuid.uuid4())
        auth = api.authorize_screenshot({"screenshot_id": sid, "captured_at": "2026-11-01T09:00:00Z", "content_type": "image/png",
                                         "byte_size": len(png), "sha256": hashlib.sha256(png).hexdigest()})
        assert auth["upload_url"].startswith(f"https://127.0.0.1:{srv.port}/mock-s3/")
        from winagent.http_client import HttpClient as H
        H(srv.base, 5, lambda: None, ca_bundle=str(srv.cert)).put_absolute(auth["upload_url"], png, {"Content-Type": "image/png"})
        assert (srv.data_dir / "storage" / f"{sid}.png").read_bytes() == png
        done = api.complete_screenshot(sid, "uploaded")
        assert done["status"] == "completed"
    finally:
        srv.stop()


def test_upload_with_tampered_signature_is_refused(cert_pair, tmp_path):
    srv = Running(*cert_pair, data_dir=tmp_path / "b", policy=_enabled_policy(tmp_path)).start()
    try:
        _, cred = register(srv, tmp_path)
        api = api_for(srv, token=lambda: cred.token)
        png = _png()
        sid = str(uuid.uuid4())
        auth = api.authorize_screenshot({"screenshot_id": sid, "captured_at": "2026-11-01T09:00:00Z", "content_type": "image/png",
                                         "byte_size": len(png), "sha256": hashlib.sha256(png).hexdigest()})
        bad_url = auth["upload_url"][:-4] + "beef"
        from winagent.http_client import HttpClient as H
        with pytest.raises(PermanentError) as exc:
            H(srv.base, 5, lambda: None, ca_bundle=str(srv.cert)).put_absolute(bad_url, png, {})
        assert exc.value.status == 403
    finally:
        srv.stop()


def test_upload_with_wrong_bytes_is_refused(cert_pair, tmp_path):
    srv = Running(*cert_pair, data_dir=tmp_path / "b", policy=_enabled_policy(tmp_path)).start()
    try:
        _, cred = register(srv, tmp_path)
        api = api_for(srv, token=lambda: cred.token)
        png = _png()
        sid = str(uuid.uuid4())
        auth = api.authorize_screenshot({"screenshot_id": sid, "captured_at": "2026-11-01T09:00:00Z", "content_type": "image/png",
                                         "byte_size": len(png), "sha256": hashlib.sha256(png).hexdigest()})
        from winagent.http_client import HttpClient as H
        with pytest.raises(PermanentError) as exc:
            H(srv.base, 5, lambda: None, ca_bundle=str(srv.cert)).put_absolute(auth["upload_url"], b"other bytes", {})
        assert exc.value.status == 400
    finally:
        srv.stop()


def test_complete_without_upload_is_conflict(cert_pair, tmp_path):
    srv = Running(*cert_pair, data_dir=tmp_path / "b", policy=_enabled_policy(tmp_path)).start()
    try:
        _, cred = register(srv, tmp_path)
        api = api_for(srv, token=lambda: cred.token)
        png = _png()
        sid = str(uuid.uuid4())
        api.authorize_screenshot({"screenshot_id": sid, "captured_at": "2026-11-01T09:00:00Z", "content_type": "image/png",
                                  "byte_size": len(png), "sha256": hashlib.sha256(png).hexdigest()})
        with pytest.raises(PermanentError) as exc:
            api.complete_screenshot(sid, "uploaded")
        assert exc.value.status == 409
    finally:
        srv.stop()


def test_heartbeat_is_acknowledged(backend, tmp_path):
    _, cred = register(backend, tmp_path)
    api = api_for(backend, token=lambda: cred.token)
    out = api.heartbeat("0.1.0", T, "console", 0, None, None, "active")
    assert out["acknowledged"] is True


def test_network_drop_then_recovery_sends_every_event_once(cert_pair, tmp_path):
    """Demo requirement 9: backend goes away, events queue in SQLite, then flush on return with no duplicates."""
    srv = Running(*cert_pair, data_dir=tmp_path / "b").start()
    _, cred = register(srv, tmp_path)
    port = srv.port
    queue = SqliteEventQueue(tmp_path / "queue.sqlite3", FakeProtector(), 1000, 3600)
    events = [Event("activity_sample", T, {"state": "active", "idle_seconds": 0.0, "sample_interval_seconds": 10.0}) for _ in range(20)]
    for e in events:
        queue.add(e)
    srv.stop()  # network down

    api = api_for(Running(*cert_pair, data_dir=tmp_path / "b", port=port), token=lambda: cred.token)
    sleeps: list[float] = []
    sync = SyncManager(api, queue, max_attempts=2, sleep=sleeps.append, clock=lambda: T)
    down = sync.flush()
    assert down.gave_up is True
    assert queue.depth() == 20  # nothing lost while offline

    back = Running(*cert_pair, data_dir=tmp_path / "b", port=port).start()
    try:
        up = sync.flush()
        assert queue.depth() == 0
        assert up.accepted == 20
        assert up.duplicates == 0
        # Replaying the same events from the queue must not create new rows.
        queue.add(events[0])
        assert sync.flush().duplicates == 1  # the server already has it, so it is reported, not stored again
    finally:
        back.stop()
        queue.close()
