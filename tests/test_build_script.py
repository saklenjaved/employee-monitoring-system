"""build.ps1 -CheckOnly: tool checks and signing settings, without building anything. Windows PowerShell only."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
POWERSHELL = shutil.which("powershell")

pytestmark = pytest.mark.skipif(not (sys.platform == "win32" and POWERSHELL), reason="needs Windows PowerShell")


def check(tmp_path, **env):
    # ISCC_PATH points at any existing file, so the test does not depend on Inno Setup being installed.
    environment = {k: v for k, v in os.environ.items() if k not in ("PFX_PATH", "PFX_PASSWORD", "SIGNTOOL_PATH", "TIMESTAMP_URL")}
    environment["ISCC_PATH"] = sys.executable
    environment.update(env)
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "build.ps1"), "-CheckOnly"],
        capture_output=True, text=True, env=environment, cwd=ROOT, timeout=120,
    )


def test_without_a_certificate_the_build_is_allowed_and_says_unsigned(tmp_path):
    result = check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UNSIGNED" in result.stdout + result.stderr


def test_a_missing_certificate_file_stops_the_build(tmp_path):
    result = check(tmp_path, PFX_PATH=str(tmp_path / "missing.pfx"))
    assert result.returncode != 0
    assert "PFX_PATH" in result.stdout + result.stderr


def test_a_certificate_without_a_password_stops_the_build(tmp_path):
    pfx = tmp_path / "cert.pfx"
    pfx.write_bytes(b"x")
    result = check(tmp_path, PFX_PATH=str(pfx))
    assert result.returncode != 0
    assert "PFX_PASSWORD" in result.stdout + result.stderr


def test_a_certificate_without_signtool_stops_the_build(tmp_path):
    pfx = tmp_path / "cert.pfx"
    pfx.write_bytes(b"x")
    result = check(tmp_path, PFX_PATH=str(pfx), PFX_PASSWORD="secret-pass", SIGNTOOL_PATH=str(tmp_path / "no-signtool.exe"))
    assert result.returncode != 0
    assert "SIGNTOOL_PATH" in result.stdout + result.stderr


def test_the_password_is_never_printed(tmp_path):
    pfx = tmp_path / "cert.pfx"
    pfx.write_bytes(b"x")
    result = check(tmp_path, PFX_PATH=str(pfx), PFX_PASSWORD="secret-pass-123", SIGNTOOL_PATH=str(tmp_path / "no.exe"))
    assert "secret-pass-123" not in result.stdout + result.stderr
