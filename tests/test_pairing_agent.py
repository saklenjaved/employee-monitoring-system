"""Agent side of connecting a PC without a code: pairing request, approval wait, credential storage."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.fake_http import ScriptedSession
from tests.fakes import FakeProtector
from winagent.api_client import AgentApi
from winagent.credentials import CredentialStore
from winagent.http_client import HttpClient, HttpError, TransientError
from winagent.pairing import dashboard_url, pair_this_pc

BASE = "https://api.example.invalid/api/agent/v1"
DETAILS = {"device_name": "LAPTOP-7", "os_version": "Windows 11", "agent_version": "0.1.0"}


def pending_request(minutes=10, request_id="req-1", token="poll-1"):
    expires = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return {
        "request_id": request_id,
        "poll_token": token,
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }


def approved(credential="cred-secret"):
    expires = datetime.now(timezone.utc) + timedelta(days=30)
    return {
        "status": "approved",
        "device_id": "6f1c2a1e-9b7d-4c1e-8a7e-2f3b4c5d6e7f",
        "credential": credential,
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }


class FakeApi:
    """Answers the pairing calls from scripted lists. An Exception in a list is raised instead."""

    def __init__(self, creates, polls):
        self.creates = list(creates)
        self.polls = list(polls)
        self.create_calls = 0
        self.poll_calls = 0

    def create_pairing_request(self, device_name, os_version, agent_version):
        self.create_calls += 1
        item = self.creates.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def poll_pairing(self, request_id, poll_token):
        self.poll_calls += 1
        item = self.polls.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def run(api, store, stop_after_waits=50):
    """Run pairing with waits that do not sleep. Stops if it never finishes."""
    waits = {"n": 0}
    opened = []

    def wait(seconds):
        waits["n"] += 1

    def stop_requested():
        return waits["n"] > stop_after_waits

    result = pair_this_pc(api, store, DETAILS, "https://h.invalid/dashboard/me/", stop_requested, wait, opened.append)
    return result, opened


def test_approval_after_waiting_stores_the_credential(tmp_path: Path):
    store = CredentialStore(tmp_path / "cred.bin", FakeProtector())
    api = FakeApi([pending_request()], [{"status": "pending"}, {"status": "pending"}, approved()])
    result, opened = run(api, store)
    assert result is True
    assert store.load().token == "cred-secret"
    assert api.poll_calls == 3
    assert opened == ["https://h.invalid/dashboard/me/"]  # the dashboard is opened once


def test_the_dashboard_address_from_the_server_is_opened_instead_of_the_api_address(tmp_path: Path):
    store = CredentialStore(tmp_path / "cred.bin", FakeProtector())
    request = {**pending_request(), "dashboard_url": "http://site.example.invalid/dashboard/me/"}
    result, opened = run(FakeApi([request], [approved()]), store)
    assert result is True
    assert opened == ["http://site.example.invalid/dashboard/me/"]


def test_an_address_that_is_not_http_is_ignored(tmp_path: Path):
    store = CredentialStore(tmp_path / "cred.bin", FakeProtector())
    request = {**pending_request(), "dashboard_url": "file:///C:/Windows/system32/calc.exe"}
    result, opened = run(FakeApi([request], [approved()]), store)
    assert result is True
    assert opened == ["https://h.invalid/dashboard/me/"]  # the derived address is used instead


def test_an_expired_request_is_replaced_by_a_new_one(tmp_path: Path):
    store = CredentialStore(tmp_path / "cred.bin", FakeProtector())
    api = FakeApi(
        [pending_request(request_id="old", token="t1"), pending_request(request_id="new", token="t2")],
        [{"status": "expired"}, approved()],
    )
    result, opened = run(api, store)
    assert result is True
    assert api.create_calls == 2
    assert len(opened) == 1  # the dashboard is opened once, not again for the replacement request


def test_a_lost_credential_starts_a_new_request(tmp_path: Path):
    store = CredentialStore(tmp_path / "cred.bin", FakeProtector())
    api = FakeApi(
        [pending_request(request_id="a", token="t1"), pending_request(request_id="b", token="t2")],
        [{"status": "delivered"}, approved()],
    )
    result, _ = run(api, store)
    assert result is True
    assert api.create_calls == 2


def test_network_failures_are_retried(tmp_path: Path):
    store = CredentialStore(tmp_path / "cred.bin", FakeProtector())
    api = FakeApi([TransientError(None, "ConnectionError"), pending_request()], [approved()])
    result, _ = run(api, store)
    assert result is True
    assert api.create_calls == 2


def test_a_wrong_poll_token_ends_the_request_and_starts_again(tmp_path: Path):
    store = CredentialStore(tmp_path / "cred.bin", FakeProtector())
    api = FakeApi(
        [pending_request(request_id="a", token="t1"), pending_request(request_id="b", token="t2")],
        [HttpError(404, "http_404"), approved()],
    )
    result, _ = run(api, store)
    assert result is True
    assert store.load() is not None


def test_stop_request_ends_pairing_without_a_credential(tmp_path: Path):
    store = CredentialStore(tmp_path / "cred.bin", FakeProtector())
    api = FakeApi([], [])
    result = pair_this_pc(api, store, DETAILS, "https://h.invalid/dashboard/me/", lambda: True, lambda s: None, lambda u: None)
    assert result is False
    assert api.create_calls == 0
    assert store.load() is None


def test_the_dashboard_address_comes_from_the_api_address():
    assert dashboard_url(BASE) == "https://api.example.invalid/dashboard/me/"
    assert dashboard_url("https://h.invalid/api/agent/v1/") == "https://h.invalid/dashboard/me/"


def test_pairing_calls_send_the_poll_token_as_a_bearer_and_no_device_credential():
    session = ScriptedSession([(201, pending_request()), (200, {"status": "pending"})])
    api = AgentApi(HttpClient(BASE, 5, lambda: None, session=session))
    created = api.create_pairing_request("LAPTOP-7", "Windows 11", "0.1.0", "org-token-1")
    api.poll_pairing(created["request_id"], created["poll_token"])
    assert "Authorization" not in session.headers_of(0)
    assert session.headers_of(1)["Authorization"] == "Bearer poll-1"
    assert session.calls[1]["url"].endswith(f"pairing/requests/{created['request_id']}/")


def test_rejected_or_rate_limited_requests_wait_longer_so_the_limit_is_not_used_up(tmp_path: Path):
    from winagent.pairing import REJECTED_RETRY_SECONDS

    store = CredentialStore(tmp_path / "cred.bin", FakeProtector())
    api = FakeApi([HttpError(429, "http_429"), HttpError(400, "http_400"), pending_request()], [approved()])
    waits = []
    result = pair_this_pc(
        api, store, DETAILS, "https://h.invalid/dashboard/me/", lambda: False, waits.append, lambda url: None
    )
    assert result is True
    assert api.create_calls == 3
    assert waits[:2] == [REJECTED_RETRY_SECONDS, REJECTED_RETRY_SECONDS]


def test_the_dashboard_opens_once_even_across_restarts(tmp_path: Path):
    from winagent.pairing import open_dashboard_once

    marker = tmp_path / "dashboard_opened"
    opened = []
    open_dashboard_once("https://h.invalid/dashboard/me/", marker, opened.append)
    open_dashboard_once("https://h.invalid/dashboard/me/", marker, opened.append)  # a restart or a new sign-in
    assert opened == ["https://h.invalid/dashboard/me/"]
    marker.unlink()  # the installer deletes it on a new install
    open_dashboard_once("https://h.invalid/dashboard/me/", marker, opened.append)
    assert len(opened) == 2


def test_a_browser_that_cannot_open_is_tried_again_and_never_stops_pairing(tmp_path: Path):
    from winagent.pairing import open_dashboard_once

    marker = tmp_path / "dashboard_opened"

    def broken(_url):
        raise OSError("no browser")

    open_dashboard_once("https://h.invalid/dashboard/me/", marker, broken)
    assert not marker.exists()
    open_dashboard_once("https://h.invalid/dashboard/me/", marker, lambda _u: False)
    assert not marker.exists()
    open_dashboard_once("https://h.invalid/dashboard/me/", marker, lambda _u: True)
    assert marker.exists()


def test_a_registered_pc_does_not_pair_again_or_open_the_dashboard(tmp_path: Path, monkeypatch):
    """Reinstall over a PC that already has its credential: no pairing request and no browser window."""
    from datetime import datetime, timedelta, timezone

    from winagent import __main__ as entry
    from winagent.credentials import DeviceCredential

    cfg_file = tmp_path / "agent.toml"
    cfg_file.write_text(
        f'[api]\nbase_url = "{BASE}"\norg_token = "tok"\n[paths]\ndata_dir = "{tmp_path.as_posix()}"\n', encoding="utf-8"
    )
    monkeypatch.setattr(entry, "_protector", lambda: FakeProtector())
    CredentialStore(tmp_path / "credential.bin", FakeProtector()).save(
        DeviceCredential("6f1c2a1e-9b7d-4c1e-8a7e-2f3b4c5d6e7f", "cred", datetime.now(timezone.utc) + timedelta(days=20))
    )
    paired, opened, ran = [], [], []
    monkeypatch.setattr(entry, "_pair_this_pc", lambda cfg, config: paired.append(1) or True)
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)

    class FakeAgent:
        health = None

        def run_forever(self, stop):
            ran.append(1)
            stop.set()

    import winagent.bootstrap as bootstrap

    monkeypatch.setattr(bootstrap, "build_agent", lambda cfg, version: (FakeAgent(), None))
    assert entry._run(entry.load_config(cfg_file), cfg_file) == 0
    assert ran == [1]  # the agent runs with the saved credential
    assert paired == [] and opened == []  # no new pairing, no browser window


def test_the_installer_clears_the_marker_the_agent_writes_and_starts_the_agent_only_after_install():
    """A fresh install must open the dashboard once, so the installer deletes the agent's marker file."""
    from winagent.pairing import DASHBOARD_MARKER

    script = (Path(__file__).resolve().parent.parent / "installer" / "winagent.iss").read_text(encoding="utf-8")
    assert "{localappdata}" + "\\WinAgent\\" + DASHBOARD_MARKER in script
    # The agent is started only from [Run], which Setup runs after a successful install. A failed install starts nothing.
    before_run, _, run_section = script.partition("[Run]")
    assert "winagent.exe" not in before_run.split("[Code]")[0]
    assert 'Parameters: "run --config' in run_section
