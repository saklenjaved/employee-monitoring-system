"""Manual checks that need a real Windows machine (docs/test-plan.md, section M).

Deselected by default (pyproject addopts). Run on a Windows 10 or 11 test device with:
  .venv\\Scripts\\python -m pytest -m windows_manual -v
These checks read real OS state. They never read keys, clipboard or typed text.
"""

import sys

import pytest

pytestmark = [pytest.mark.windows_manual, pytest.mark.skipif(sys.platform != "win32", reason="Windows only")]


def test_m01_dpapi_round_trip_and_not_plaintext():
    from winagent.platform_win import DpapiProtector

    p = DpapiProtector()
    blob = p.protect(b"SECRET-VALUE")
    assert b"SECRET-VALUE" not in blob
    assert p.unprotect(blob) == b"SECRET-VALUE"


def test_m02_foreground_window_returns_process_name():
    from winagent.platform_win import Win32ForegroundWindowReader

    info = Win32ForegroundWindowReader().read(include_title=False)
    assert info is None or (info.process_name.endswith(".exe") and info.window_title is None)


def test_m03_idle_time_is_non_negative_and_grows_without_input():
    import time

    from winagent.platform_win import Win32IdleTimeReader

    reader = Win32IdleTimeReader()
    first = reader.idle_seconds()
    time.sleep(2)
    assert first >= 0
    assert reader.idle_seconds() >= first  # only true if no input happened in the 2 s; repeat if you touched the mouse


def test_m04_screen_capture_returns_png():
    from winagent.platform_win import MssScreenCapturer

    png = MssScreenCapturer().capture_png()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_m05_session_type_is_console_on_local_desktop():
    from winagent.platform_win import Win32SessionInspector

    assert Win32SessionInspector().session_type() == "console"


def test_m06_autostart_enable_and_disable_per_user():
    from winagent.platform_win import WinregAutostart

    reg = WinregAutostart()
    reg.enable('"python" -m winagent run --config "x"')
    try:
        assert reg.is_enabled()
    finally:
        reg.disable()
    assert not reg.is_enabled()
