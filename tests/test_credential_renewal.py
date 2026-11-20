"""The installed agent renews its own credential, and asks to connect again when the server rejects it."""

import threading
from datetime import datetime, timedelta, timezone

from tests.test_launcher import TEN_AM, Harness
from winagent.credentials import DeviceCredential

DEVICE_ID = "6f1c2a1e-9b7d-4c1e-8a7e-2f3b4c5d6e7f"
EXPIRES_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def expiring_in(h, days):
    """Replace the stored credential with one that expires `days` from TEN_AM."""
    h.store.save(DeviceCredential(DEVICE_ID, "old-token", TEN_AM + timedelta(days=days)))


def test_a_credential_close_to_expiry_is_renewed_and_saved(tmp_path):
    new_expiry = (TEN_AM + timedelta(days=30)).astimezone(timezone.utc).strftime(EXPIRES_FORMAT)
    h = Harness(tmp_path, [(200, {"credential": "new-token", "expires_at": new_expiry})])
    expiring_in(h, 2)
    h.agent._renew_if_due(TEN_AM, 0.0)
    saved = h.store.load()
    assert saved.token == "new-token"
    assert saved.device_id == DEVICE_ID
    assert not h.agent.needs_pairing


def test_a_credential_with_time_left_is_not_renewed(tmp_path):
    h = Harness(tmp_path, [])
    expiring_in(h, 20)
    h.agent._renew_if_due(TEN_AM, 0.0)
    assert h.http.calls == []
    assert h.store.load().token == "old-token"


def test_a_rejected_renewal_asks_the_pc_to_connect_again(tmp_path):
    h = Harness(tmp_path, [(401, {"detail": "credential expired; re-register"})])
    expiring_in(h, 1)
    h.agent._renew_if_due(TEN_AM, 0.0)
    assert h.agent.needs_pairing


def test_a_pc_asked_to_connect_again_stops_the_run_loop(tmp_path):
    h = Harness(tmp_path, [])
    h.sync.auth_failed = True
    stop = threading.Event()
    h.agent.run_forever(stop, tick_seconds=0)
    assert h.http.calls == []
