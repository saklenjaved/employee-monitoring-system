from datetime import datetime, timezone

import pytest

from winagent.credentials import CredentialError, CredentialStore, DeviceCredential
from tests.fakes import FakeProtector

CRED = DeviceCredential(
    device_id="6f1c2a1e-9b7d-4c1e-8a7e-2f3b4c5d6e7f",
    token="SECRET-TOKEN-VALUE",
    expires_at=datetime(2026, 12, 1, tzinfo=timezone.utc),
)


def test_round_trip_and_no_plaintext_on_disk(tmp_path):
    store = CredentialStore(tmp_path / "credential.bin", FakeProtector())
    store.save(CRED)
    raw = (tmp_path / "credential.bin").read_bytes()
    assert b"SECRET-TOKEN-VALUE" not in raw
    loaded = store.load()
    assert loaded == CRED


def test_repr_hides_token():
    assert "SECRET-TOKEN-VALUE" not in repr(CRED)


def test_load_missing_returns_none(tmp_path):
    assert CredentialStore(tmp_path / "none.bin", FakeProtector()).load() is None


def test_corrupt_blob_requires_reregistration(tmp_path):
    path = tmp_path / "credential.bin"
    path.write_bytes(b"garbage")
    with pytest.raises(CredentialError, match="re-registration"):
        CredentialStore(path, FakeProtector()).load()


def test_clear_removes_file(tmp_path):
    store = CredentialStore(tmp_path / "credential.bin", FakeProtector())
    store.save(CRED)
    store.clear()
    assert store.load() is None
