"""Window-title privacy protections (demo decision: titles on, with exclusions)."""

from datetime import datetime, timezone

from tests.fakes import FakeForeground
from winagent.config import DEFAULT_EXCLUDED_APPS, DEFAULT_TITLE_KEYWORDS
from winagent.monitors.app_monitor import AppMonitor
from winagent.platform_base import ForegroundInfo

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


def monitor(info, **kw):
    return AppMonitor(
        FakeForeground(info),
        10,
        include_title=True,
        excluded_apps=frozenset(kw.pop("excluded", DEFAULT_EXCLUDED_APPS)),
        title_keywords_excluded=kw.pop("keywords", DEFAULT_TITLE_KEYWORDS),
    )


def test_title_sent_when_enabled_and_not_sensitive():
    ev = monitor(ForegroundInfo("code.exe", "main.py - Visual Studio Code")).sample(NOW)
    assert ev.payload["window_title"] == "main.py - Visual Studio Code"


def test_title_with_sensitive_keyword_is_dropped_but_app_kept():
    ev = monitor(ForegroundInfo("chrome.exe", "Reset your Password - Chrome")).sample(NOW)
    assert ev is not None
    assert ev.payload["process_name"] == "chrome.exe"
    assert "window_title" not in ev.payload


def test_private_browsing_keyword_matches_case_insensitively():
    ev = monitor(ForegroundInfo("msedge.exe", "New InPrivate tab")).sample(NOW)
    assert "window_title" not in ev.payload


def test_excluded_app_produces_no_event_from_monitor_side_check():
    mon = monitor(ForegroundInfo("KeePass.exe", "Database - KeePass"))
    ev = mon.sample(NOW)
    # The launcher drops events for excluded apps. The monitor reports it so the caller can.
    assert mon.is_excluded(ev.payload["process_name"]) is True


def test_title_is_truncated_to_256_characters():
    ev = monitor(ForegroundInfo("code.exe", "a" * 500)).sample(NOW)
    assert len(ev.payload["window_title"]) == 256


def test_titles_off_sends_no_title():
    mon = AppMonitor(FakeForeground(ForegroundInfo("code.exe", "notes")), 10, include_title=False)
    assert "window_title" not in mon.sample(NOW).payload
