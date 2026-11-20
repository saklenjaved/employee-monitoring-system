"""The heartbeat carries the focused app, and the window title only when titles are allowed."""
from datetime import datetime, timezone

from tests.fakes import FakeForeground
from winagent.monitors.app_monitor import AppMonitor
from winagent.platform_base import ForegroundInfo

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def monitor(info, include_title, excluded=frozenset()):
    reader = FakeForeground(info=info)
    app = AppMonitor(reader, sample_interval_seconds=30, include_title=include_title, excluded_apps=excluded)
    app.sample(NOW)
    return app


def test_nothing_is_reported_before_the_first_sample():
    app = AppMonitor(FakeForeground(info=None), sample_interval_seconds=30)
    assert app.focus_for_heartbeat() == {}


def test_the_app_name_is_sent_without_the_title_when_titles_are_off():
    app = monitor(ForegroundInfo("notepad.exe", None), include_title=False)
    assert app.focus_for_heartbeat() == {"focus_app": "notepad.exe", "focus_title": ""}


def test_the_title_is_sent_when_titles_are_allowed():
    app = monitor(ForegroundInfo("notepad.exe", "Budget.txt"), include_title=True)
    assert app.focus_for_heartbeat() == {"focus_app": "notepad.exe", "focus_title": "Budget.txt"}


def test_a_blocked_title_is_never_sent():
    app = AppMonitor(
        FakeForeground(info=ForegroundInfo("chrome.exe", "Bank login")),
        sample_interval_seconds=30,
        include_title=True,
        title_keywords_excluded=("bank",),
    )
    app.sample(NOW)
    assert app.focus_for_heartbeat() == {"focus_app": "chrome.exe", "focus_title": ""}


def test_an_excluded_app_sends_no_focus():
    app = monitor(ForegroundInfo("secret.exe", "Doc"), include_title=True, excluded=frozenset({"secret.exe"}))
    assert app.focus_for_heartbeat() == {}


def test_no_focus_window_sends_no_focus():
    app = monitor(None, include_title=True)
    assert app.focus_for_heartbeat() == {}
