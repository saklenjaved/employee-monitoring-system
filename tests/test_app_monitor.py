from datetime import datetime, timezone

from tests.fakes import FakeForeground
from winagent.monitors.app_monitor import AppMonitor
from winagent.platform_base import ForegroundInfo

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


def test_emits_app_focus_event_without_title_by_default():
    reader = FakeForeground(ForegroundInfo("chrome.exe", "Secret inbox title"))
    ev = AppMonitor(reader, sample_interval_seconds=30).sample(NOW)
    assert ev.event_type == "app_focus"
    assert ev.payload == {"process_name": "chrome.exe", "duration_seconds": 30.0}
    assert reader.last_include_title is False


def test_title_included_only_when_enabled_and_truncated():
    reader = FakeForeground(ForegroundInfo("code.exe", "x" * 400))
    ev = AppMonitor(reader, 30, include_title=True).sample(NOW)
    assert len(ev.payload["window_title"]) == 256


def test_no_foreground_window_emits_nothing():
    assert AppMonitor(FakeForeground(None), 30).sample(NOW) is None


def test_windows_error_skips_sample_and_does_not_raise():
    reader = FakeForeground(raise_error=OSError("access denied"))
    assert AppMonitor(reader, 30).sample(NOW) is None


def test_exclusion_list_is_case_insensitive():
    mon = AppMonitor(FakeForeground(None), 30, excluded_apps=frozenset({"KeePass.exe"}))
    assert mon.is_excluded("keepass.exe")
    assert not mon.is_excluded("chrome.exe")
    assert not mon.is_excluded(None)
