from datetime import datetime, timezone

import pytest
import requests

from tests.fake_http import ScriptedSession
from tests.fakes import FakeProtector
from winagent.api_client import AgentApi
from winagent.credentials import CredentialStore
from winagent.http_client import AuthError, HttpClient, PermanentError, TransientError
from winagent.registration import RegistrationError, is_expired, register_device

BASE = "https://api.example.invalid/api/agent/v1"
DEVICE_ID = "6f1c2a1e-9b7d-4c1e-8a7e-2f3b4c5d6e7f"


def make_api(session, token=lambda: None):
    return AgentApi(HttpClient(BASE, 5, token, session=session))


def test_register_stores_credential_encrypted(tmp_path):
    session = ScriptedSession([(201, {"device_id": DEVICE_ID, "credential": "TOKEN-XYZ", "expires_at": "2026-12-01T00:00:00Z"})])
    store = CredentialStore(tmp_path / "c.bin", FakeProtector())
    cred = register_device(make_api(session), store, "CODE-123456", "PC-1", "Windows 11 10.0.26100", "0.1.0")
    assert cred.device_id == DEVICE_ID
    assert b"TOKEN-XYZ" not in (tmp_path / "c.bin").read_bytes()
    assert store.load().token == "TOKEN-XYZ"


def test_register_sends_no_org_or_employee_fields(tmp_path):
    session = ScriptedSession([(201, {"device_id": DEVICE_ID, "credential": "t", "expires_at": "2026-12-01T00:00:00Z"})])
    register_device(make_api(session), CredentialStore(tmp_path / "c.bin", FakeProtector()), "CODE-123456", "PC", "Win", "0.1.0")
    body = session.json_body_of(0)
    assert "organization_id" not in body and "employee_id" not in body
    assert body["architecture"] == "x64"


def test_register_uses_tls_and_no_bearer_header(tmp_path):
    session = ScriptedSession([(201, {"device_id": DEVICE_ID, "credential": "t", "expires_at": "2026-12-01T00:00:00Z"})])
    register_device(make_api(session), CredentialStore(tmp_path / "c.bin", FakeProtector()), "CODE-123456", "PC", "Win", "0.1.0")
    call = session.calls[0]
    assert call["url"].startswith("https://")
    assert call["verify"] is True
    assert "Authorization" not in call["headers"]


def test_short_code_rejected_before_network(tmp_path):
    session = ScriptedSession([])
    with pytest.raises(RegistrationError):
        register_device(make_api(session), CredentialStore(tmp_path / "c.bin", FakeProtector()), "123", "PC", "Win", "0.1.0")
    assert session.calls == []


def test_missing_fields_in_response(tmp_path):
    session = ScriptedSession([(201, {"device_id": DEVICE_ID})])
    with pytest.raises(RegistrationError, match="missing"):
        register_device(make_api(session), CredentialStore(tmp_path / "c.bin", FakeProtector()), "CODE-123456", "PC", "Win", "0.1.0")


def test_forbidden_registration_is_auth_error(tmp_path):
    session = ScriptedSession([(403, {"code": "bad_code"})])
    with pytest.raises(AuthError):
        register_device(make_api(session), CredentialStore(tmp_path / "c.bin", FakeProtector()), "CODE-123456", "PC", "Win", "0.1.0")


def test_is_expired():
    cred_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    from winagent.credentials import DeviceCredential

    cred = DeviceCredential(DEVICE_ID, "t", cred_time)
    assert is_expired(cred, datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert not is_expired(cred, datetime(2025, 12, 31, tzinfo=timezone.utc))


def test_http_client_rejects_plain_http():
    with pytest.raises(ValueError, match="https"):
        HttpClient("http://api.example.invalid", 5, lambda: None, session=ScriptedSession([]))


def test_401_is_auth_error_and_token_never_in_message():
    session = ScriptedSession([(401, {"code": "expired"})])
    http = HttpClient(BASE, 5, lambda: "SECRET-TOKEN", session=session)
    with pytest.raises(AuthError) as exc:
        http.get_json("config/")
    assert "SECRET-TOKEN" not in str(exc.value)


def test_500_and_429_are_transient_and_other_4xx_permanent():
    http = HttpClient(BASE, 5, lambda: "t", session=ScriptedSession([(500, {}), (429, {}), (400, {})]))
    with pytest.raises(TransientError):
        http.get_json("config/")
    with pytest.raises(TransientError):
        http.get_json("config/")
    with pytest.raises(PermanentError):
        http.get_json("config/")


def test_dropped_connection_is_transient_and_hides_url():
    session = ScriptedSession([requests.ConnectionError("https://secret.example/?token=abc")])
    http = HttpClient(BASE, 5, lambda: "t", session=session)
    with pytest.raises(TransientError) as exc:
        http.get_json("config/")
    assert "token=abc" not in str(exc.value)
    assert exc.value.code == "ConnectionError"


def test_missing_token_fails_before_network():
    session = ScriptedSession([])
    http = HttpClient(BASE, 5, lambda: None, session=session)
    with pytest.raises(AuthError):
        http.get_json("config/")
    assert session.calls == []
