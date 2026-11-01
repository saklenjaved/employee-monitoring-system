"""Installation and release checks (TAD 7, TAD 12 'Installation' and 'Release'). Fakes only; real installs are manual."""

import re
from pathlib import Path

from tests.fakes import FakeAutostart
from winagent import __version__
from winagent.__main__ import main

ROOT = Path(__file__).resolve().parent.parent


def test_version_matches_pyproject():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(rf'^version = "{re.escape(__version__)}"$', text, re.M)


def test_build_script_has_signing_placeholder_and_no_admin_steps():
    script = (ROOT / "build.ps1").read_text(encoding="utf-8")
    assert "Code signing placeholder" in script
    assert "UNSIGNED" in script
    assert "RunAs" not in script and "Start-Process -Verb" not in script  # no elevation
    assert "pytest" in script  # tests gate the build


def test_entry_script_calls_main():
    assert "from winagent.__main__ import main" in (ROOT / "scripts" / "entry.py").read_text(encoding="utf-8")


def test_config_example_is_https_and_has_no_secrets():
    example = ROOT / "config.example.toml"
    text = example.read_text(encoding="utf-8")
    assert "https://" in text and "http://" not in text
    assert not re.search(r"(?i)(token|password|secret|credential)\s*=", text)


def test_status_before_first_run_reports_no_data(tmp_path, capsys):
    cfg = tmp_path / "agent.toml"
    cfg.write_text(f'[api]\nbase_url = "https://api.example.invalid/api/agent/v1"\n[paths]\ndata_dir = "{tmp_path.as_posix()}"\n', encoding="utf-8")
    assert main(["status", "--config", str(cfg)]) == 1
    assert "no health data" in capsys.readouterr().out


def test_unregister_removes_credential_file(tmp_path, monkeypatch):
    from winagent import __main__ as entry
    from tests.fakes import FakeProtector
    from winagent.credentials import CredentialStore, DeviceCredential
    from datetime import datetime, timezone

    cfg = tmp_path / "agent.toml"
    cfg.write_text(f'[api]\nbase_url = "https://api.example.invalid/api/agent/v1"\n[paths]\ndata_dir = "{tmp_path.as_posix()}"\n', encoding="utf-8")
    monkeypatch.setattr(entry, "_protector", lambda: FakeProtector())
    store = CredentialStore(tmp_path / "credential.bin", FakeProtector())
    store.save(DeviceCredential("6f1c2a1e-9b7d-4c1e-8a7e-2f3b4c5d6e7f", "t", datetime(2027, 1, 1, tzinfo=timezone.utc)))
    assert main(["unregister", "--config", str(cfg)]) == 0
    assert store.load() is None


def test_autostart_fake_is_per_user_only():
    # The real implementation writes HKCU only (see platform_win.WinregAutostart). Check the source.
    src = (ROOT / "winagent" / "platform_win.py").read_text(encoding="utf-8")
    assert "HKEY_CURRENT_USER" in src and "HKEY_LOCAL_MACHINE" not in src
    assert FakeAutostart().is_enabled() is False


def test_dpapi_is_user_scope_only():
    src = (ROOT / "winagent" / "platform_win.py").read_text(encoding="utf-8")
    assert "CRYPTPROTECT_LOCAL_MACHINE" not in src
    assert "CRYPTPROTECT_UI_FORBIDDEN" in src
