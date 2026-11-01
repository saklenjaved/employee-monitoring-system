"""Local mock backend for the Windows agent demo (HTTPS, stdlib only, no AWS).

Implements the six capabilities in docs/api-contract.yaml under /api/agent/v1:
registration, policy/config, event ingestion, screenshot upload authorization,
screenshot completion, and heartbeat. Screenshot bytes go to local storage on disk
(a stand-in for S3), never to an external service.

It is a demo, not a backend. Device tokens are stored as SHA-256 hashes. Event payloads are
stored in clear in the local demo database so you can inspect them with `python -m mock_backend.show`.

Run:
  python -m mock_backend.server --cert mock_backend/certs/cert.pem --key mock_backend/certs/key.pem
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
import ssl
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

API_PREFIX = "/api/agent/v1"
MOCK_STORAGE_PREFIX = "/mock-s3/"
DEFAULT_DEMO_CODE = "DEMO-LOCAL-0001"
TOKEN_DAYS = 30
UPLOAD_TTL_SECONDS = 300
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_BATCH = 500
EVENT_TYPES = {"app_focus", "activity_sample", "website_visit", "session_state"}
UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
SCREENSHOT_COMPLETE = re.compile(r"^screenshots/([0-9a-f-]{36})/complete/$")

log = logging.getLogger("mock_backend")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Store:
    """SQLite-backed demo database. One lock serialises access."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, device_name TEXT,
                    os_version TEXT, agent_version TEXT, registered_at TEXT, expires_at TEXT);
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, event_type TEXT NOT NULL,
                    captured_at TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS screenshots (
                    screenshot_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, sha256 TEXT NOT NULL,
                    byte_size INTEGER NOT NULL, status TEXT NOT NULL, authorized_at TEXT,
                    stored_at TEXT, completed_at TEXT, error_code TEXT);
                CREATE TABLE IF NOT EXISTS heartbeats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL,
                    received_at TEXT NOT NULL, body TEXT NOT NULL);
                """
            )
            self._conn.commit()

    def create_device(self, device_name: str, os_version: str, agent_version: str) -> tuple[str, str, datetime]:
        device_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(32)
        now = utc_now()
        expires = now + timedelta(days=TOKEN_DAYS)
        with self._lock:
            self._conn.execute(
                "INSERT INTO devices VALUES (?, ?, ?, ?, ?, ?, ?)",
                (device_id, _hash(token), device_name, os_version, agent_version, iso(now), iso(expires)),
            )
            self._conn.commit()
        return device_id, token, expires

    def device_for_token(self, token: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT device_id, expires_at FROM devices WHERE token_hash = ?", (_hash(token),)
            ).fetchone()
        if row is None:
            return None
        return {"device_id": row[0], "expires_at": row[1]}

    def ingest(self, device_id: str, events: list[dict]) -> dict[str, list[str]]:
        accepted: list[str] = []
        duplicates: list[str] = []
        rejected: list[str] = []
        received = iso(utc_now())
        with self._lock:
            for ev in events:
                eid = ev["event_id"]
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?, ?)",
                    (eid, device_id, ev["event_type"], ev["captured_at"], received, json.dumps(ev["payload"])),
                )
                (accepted if cur.rowcount == 1 else duplicates).append(eid)
            self._conn.commit()
        return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected}

    def authorize_screenshot(self, screenshot_id: str, device_id: str, sha256: str, byte_size: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO screenshots (screenshot_id, device_id, sha256, byte_size, status, authorized_at)"
                " VALUES (?, ?, ?, ?, 'authorized', ?)",
                (screenshot_id, device_id, sha256, byte_size, iso(utc_now())),
            )
            self._conn.commit()

    def screenshot(self, screenshot_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT screenshot_id, device_id, sha256, byte_size, status FROM screenshots WHERE screenshot_id = ?",
                (screenshot_id,),
            ).fetchone()
        if row is None:
            return None
        return {"screenshot_id": row[0], "device_id": row[1], "sha256": row[2], "byte_size": row[3], "status": row[4]}

    def mark_stored(self, screenshot_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE screenshots SET status = 'stored', stored_at = ? WHERE screenshot_id = ?", (iso(utc_now()), screenshot_id))
            self._conn.commit()

    def complete_screenshot(self, screenshot_id: str, status: str, error_code: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE screenshots SET status = ?, completed_at = ?, error_code = ? WHERE screenshot_id = ?",
                (status, iso(utc_now()), error_code, screenshot_id),
            )
            self._conn.commit()

    def add_heartbeat(self, device_id: str, body: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO heartbeats (device_id, received_at, body) VALUES (?, ?, ?)",
                (device_id, iso(utc_now()), json.dumps(body)),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class MockBackendServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, store: Store, policy: dict, storage_dir: Path, codes: set[str], signing_key: bytes, public_base: str):
        super().__init__(address, MockHandler)
        self.store = store
        self.policy = policy
        self.storage_dir = storage_dir
        self.codes = codes
        self.signing_key = signing_key
        self.public_base = public_base
        storage_dir.mkdir(parents=True, exist_ok=True)


class MockHandler(BaseHTTPRequestHandler):
    server: MockBackendServer
    timeout = 30
    server_version = "WinAgentMock/0.1"

    # Logs method, path and status only. Never headers, bodies or query strings (they hold tokens and signatures).
    def log_message(self, format: str, *args) -> None:
        log.info("%s %s", self.command, urlparse(self.path).path)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def _dispatch(self, method: str) -> None:
        path = urlparse(self.path).path
        try:
            if method == "PUT" and path.startswith(MOCK_STORAGE_PREFIX):
                return self._mock_storage_put(path)
            if not path.startswith(API_PREFIX + "/"):
                return self._error(404, "not_found", "unknown path")
            route = path[len(API_PREFIX) + 1 :]  # e.g. "config/"
            if method == "POST" and route == "devices/register/":
                return self._register()
            if method == "GET" and route == "config/":
                return self._config()
            if method == "POST" and route == "events/batch/":
                return self._events()
            if method == "POST" and route == "screenshots/authorize/":
                return self._authorize_screenshot()
            if method == "POST" and route == "heartbeat/":
                return self._heartbeat()
            match = SCREENSHOT_COMPLETE.match(route)
            if method == "POST" and match:
                return self._complete_screenshot(match.group(1))
            return self._error(404, "not_found", "unknown endpoint")
        except _BadRequest as exc:
            return self._error(400, "invalid_request", str(exc))
        except _TooLarge:
            return self._error(413, "payload_too_large", "request body too large")

    # --- helpers -----------------------------------------------------------------

    def _read_json(self, limit: int = MAX_JSON_BYTES) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            raise _BadRequest("empty body")
        if length > limit:
            raise _TooLarge()
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError as exc:
            raise _BadRequest("body is not valid JSON") from exc
        if not isinstance(data, dict):
            raise _BadRequest("body must be a JSON object")
        return data

    def _send(self, status: int, body: dict | None = None) -> None:
        raw = json.dumps(body or {}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, status: int, code: str, detail: str) -> None:
        self._send(status, {"code": code, "detail": detail})

    def _device(self) -> dict | None:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or len(auth) < 15:
            self._error(401, "missing_credential", "device credential required")
            return None
        device = self.server.store.device_for_token(auth[len("Bearer ") :].strip())
        if device is None:
            self._error(401, "invalid_credential", "credential not recognised; re-register")
            return None
        if datetime.fromisoformat(device["expires_at"]) <= utc_now():
            self._error(401, "credential_expired", "credential expired; re-register")
            return None
        return device

    # --- endpoints -----------------------------------------------------------------

    def _register(self) -> None:
        body = self._read_json()
        code = str(body.get("registration_code", ""))
        if code not in self.server.codes:
            return self._error(403, "invalid_registration_code", "registration code not recognised")
        for field in ("device_name", "os_version", "agent_version"):
            if not isinstance(body.get(field), str):
                raise _BadRequest(f"{field} is required")
        device_id, token, expires = self.server.store.create_device(body["device_name"], body["os_version"], body["agent_version"])
        # organization_id and employee_id in the body are ignored: ownership comes from the device record.
        self._send(201, {"device_id": device_id, "credential": token, "expires_at": iso(expires)})

    def _config(self) -> None:
        if self._device() is None:
            return
        self._send(200, self.server.policy)

    def _events(self) -> None:
        device = self._device()
        if device is None:
            return
        body = self._read_json()
        events = body.get("events")
        if not isinstance(events, list) or not 1 <= len(events) <= MAX_BATCH:
            raise _BadRequest(f"events must be a list of 1..{MAX_BATCH}")
        valid: list[dict] = []
        rejected: list[str] = []
        for ev in events:
            if _valid_event(ev):
                valid.append(ev)
            elif isinstance(ev, dict) and isinstance(ev.get("event_id"), str) and UUID4.match(ev["event_id"]):
                rejected.append(ev["event_id"])  # only well-formed ids are echoed back
        result = self.server.store.ingest(device["device_id"], valid)
        result["rejected"] = rejected
        self._send(200, result)

    def _authorize_screenshot(self) -> None:
        device = self._device()
        if device is None:
            return
        body = self._read_json()
        sid = body.get("screenshot_id", "")
        if not isinstance(sid, str) or not UUID4.match(sid):
            raise _BadRequest("screenshot_id must be a UUID4")
        if body.get("content_type") != "image/png":
            raise _BadRequest("content_type must be image/png")
        size = body.get("byte_size")
        if not isinstance(size, int) or not 1 <= size <= MAX_IMAGE_BYTES:
            raise _BadRequest("byte_size out of range")
        digest = body.get("sha256", "")
        if not isinstance(digest, str) or not SHA256.match(digest):
            raise _BadRequest("sha256 is malformed")
        if not self.server.policy.get("screenshots", {}).get("enabled", False):
            return self._error(403, "policy_denies", "screenshots are disabled by the demo policy")
        self.server.store.authorize_screenshot(sid, device["device_id"], digest, size)
        expires = int((utc_now() + timedelta(seconds=UPLOAD_TTL_SECONDS)).timestamp())
        sig = self._sign(sid, expires)
        url = f"{self.server.public_base}{MOCK_STORAGE_PREFIX}{sid}?expires={expires}&sig={sig}"
        self._send(
            200,
            {
                "screenshot_id": sid,
                "upload_url": url,
                "upload_method": "PUT",
                "upload_headers": {},
                "expires_at": iso(datetime.fromtimestamp(expires, tz=timezone.utc)),
            },
        )

    def _mock_storage_put(self, path: str) -> None:
        sid = path[len(MOCK_STORAGE_PREFIX) :]
        if not UUID4.match(sid):
            return self._error(404, "not_found", "unknown object")
        query = parse_qs(urlparse(self.path).query)
        try:
            expires = int(query.get("expires", ["0"])[0])
        except ValueError:
            return self._error(403, "bad_signature", "signature check failed")
        sig = query.get("sig", [""])[0]
        if expires < int(utc_now().timestamp()) or not hmac.compare_digest(sig, self._sign(sid, expires)):
            return self._error(403, "bad_signature", "upload link expired or invalid")
        record = self.server.store.screenshot(sid)
        if record is None or record["status"] not in ("authorized", "stored"):
            return self._error(403, "not_authorized", "no authorization for this object")
        length = int(self.headers.get("Content-Length", "0") or 0)
        if not 1 <= length <= MAX_IMAGE_BYTES:
            return self._error(413, "payload_too_large", "image size out of range")
        data = self.rfile.read(length)
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            return self._error(400, "checksum_mismatch", "uploaded bytes do not match the authorized hash")
        target = self.server.storage_dir / f"{sid}.png"
        target.write_bytes(data)
        self.server.store.mark_stored(sid)
        self._send(200, {})

    def _complete_screenshot(self, sid: str) -> None:
        device = self._device()
        if device is None:
            return
        body = self._read_json()
        status = body.get("status")
        if status not in ("uploaded", "failed"):
            raise _BadRequest("status must be uploaded or failed")
        record = self.server.store.screenshot(sid)
        if record is None or record["device_id"] != device["device_id"]:
            return self._error(404, "not_found", "screenshot not found for this device")
        if status == "uploaded" and record["status"] != "stored":
            return self._error(409, "object_missing", "upload not found in storage")
        self.server.store.complete_screenshot(sid, "completed" if status == "uploaded" else "upload_failed", body.get("error_code"))
        self._send(200, {"screenshot_id": sid, "status": "completed" if status == "uploaded" else "upload_failed"})

    def _heartbeat(self) -> None:
        device = self._device()
        if device is None:
            return
        body = self._read_json()
        if not isinstance(body.get("agent_version"), str) or not isinstance(body.get("session_type"), str):
            raise _BadRequest("agent_version and session_type are required")
        self.server.store.add_heartbeat(device["device_id"], body)
        self._send(200, {"acknowledged": True, "policy_changed": False})

    def _sign(self, sid: str, expires: int) -> str:
        return hmac.new(self.server.signing_key, f"{sid}|{expires}|PUT".encode("utf-8"), hashlib.sha256).hexdigest()


class _BadRequest(Exception):
    pass


class _TooLarge(Exception):
    pass


# Payload keys per event type, copied from docs/event-schema.json (additionalProperties: false).
PAYLOAD_RULES = {
    "app_focus": ({"process_name", "duration_seconds"}, {"process_name", "duration_seconds", "window_title"}),
    "activity_sample": ({"state", "idle_seconds", "sample_interval_seconds"}, {"state", "idle_seconds", "sample_interval_seconds"}),
    "website_visit": ({"domain"}, {"domain", "duration_seconds"}),
    "session_state": ({"session_type"}, {"session_type", "state"}),
}


def _valid_payload(event_type: str, payload: dict) -> bool:
    required, allowed = PAYLOAD_RULES[event_type]
    return required <= set(payload) <= allowed


def _valid_event(ev: object) -> bool:
    if not isinstance(ev, dict):
        return False
    if not isinstance(ev.get("event_id"), str) or not UUID4.match(ev["event_id"]):
        return False
    if ev.get("event_type") not in EVENT_TYPES:
        return False
    captured = ev.get("captured_at")
    if not isinstance(captured, str) or not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$", captured):
        return False
    payload = ev.get("payload")
    return isinstance(payload, dict) and _valid_payload(ev["event_type"], payload)


def create_server(
    cert: Path,
    key: Path,
    host: str = "127.0.0.1",
    port: int = 8443,
    data_dir: Path = Path("mock_backend/data"),
    policy_path: Path = Path("mock_backend/demo_policy.json"),
    codes: set[str] | None = None,
) -> MockBackendServer:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
    policy = json.loads(policy_path.read_text(encoding="utf-8-sig"))
    store = Store(data_dir / "backend.sqlite3")
    server = MockBackendServer(
        (host, port),
        store,
        policy,
        data_dir / "storage",
        codes or {DEFAULT_DEMO_CODE},
        signing_key=_signing_key(data_dir),
        public_base="",
    )
    # Use the bound port, so port 0 (tests) and real ports both produce valid upload links.
    server.public_base = f"https://{host}:{server.server_address[1]}"
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    return server


def _signing_key(data_dir: Path) -> bytes:
    """Random per-data-dir key for upload links, kept in the local data folder."""
    path = data_dir / "signing.key"
    data_dir.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(secrets.token_bytes(32))
    return path.read_bytes()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local mock backend for the Windows agent demo (HTTPS)")
    parser.add_argument("--cert", required=True, type=Path)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8443, type=int)
    parser.add_argument("--data-dir", default=Path("mock_backend/data"), type=Path)
    parser.add_argument("--policy", default=Path("mock_backend/demo_policy.json"), type=Path)
    parser.add_argument("--registration-code", action="append", dest="codes", help="allowed demo code (repeatable)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = create_server(args.cert, args.key, args.host, args.port, args.data_dir, args.policy, set(args.codes or [DEFAULT_DEMO_CODE]))
    print(f"Mock backend listening on https://{args.host}:{args.port}{API_PREFIX}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
