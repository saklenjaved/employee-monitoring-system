"""Checks one event from the agent against docs/event-schema.json.

Unknown fields are refused, so typed text or an organization/employee ID cannot
slip in. Beyond the schema there are sanity limits (marked "default, to confirm")
so one bad value cannot distort the totals.
"""
import math
import re
from datetime import datetime, timedelta, timezone

UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
# A website domain such as example.com, lower case. Classification rules use the same pattern.
DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*$")

EVENT_TYPES = ("app_focus", "activity_sample", "website_visit", "session_state")
SESSION_TYPES = ("console", "rdp", "citrix", "vdi", "unknown")
SESSION_STATES = ("monitoring", "paused_hours", "paused_policy", "unsupported_session", "locked")

# Sanity limits (default, to confirm).
MAX_CLOCK_AHEAD = timedelta(minutes=10)  # the agent's clock may run a little fast
MAX_EVENT_AGE = timedelta(days=30)  # the agent queue keeps events for 72 hours
# A sample covers at most one hour. The agent's longest sample interval is 5 minutes. Daily totals
# look this far past midnight for events that cover time before it (analytics.summaries).
MAX_DURATION_SECONDS = 60 * 60
MAX_IDLE_SECONDS = 31 * 24 * 60 * 60
MAX_SAMPLE_INTERVAL_SECONDS = 3600


def is_event_id(value):
    return isinstance(value, str) and UUID4.fullmatch(value) is not None


def _text(value, max_length, allow_empty=False):
    """A clean string, or None. NUL characters and unencodable text are refused because
    the database cannot store them."""
    if not isinstance(value, str) or len(value) > max_length:
        return None
    if (not value and not allow_empty) or "\x00" in value:
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return value


def file_name(value):
    """An executable file name such as chrome.exe, or None. Paths, control characters and
    names with leading or trailing spaces are refused, so a name cannot carry other text."""
    text = _text(value, 260)
    if text is None or text != text.strip():
        return None
    if any(char in text for char in '/\\:*?"<>|') or any(ord(char) < 32 for char in text):
        return None
    return text


def domain_name(value):
    """A lower-case website domain, or None."""
    text = _text(value, 253)
    if text is None:
        return None
    text = text.lower()
    return text if DOMAIN.fullmatch(text) else None


def _number(value, minimum, maximum):
    """A real number within limits, or None. Booleans are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value < minimum or value > maximum:
        return None
    return value


def _keys_ok(payload, required, allowed):
    keys = set(payload)
    return required <= keys <= allowed


def _app_focus(payload):
    if not _keys_ok(payload, {"process_name", "duration_seconds"}, {"process_name", "window_title", "duration_seconds"}):
        return None
    name = file_name(payload["process_name"])
    duration = _number(payload["duration_seconds"], 0, MAX_DURATION_SECONDS)
    if name is None or duration is None:
        return None
    # A window title is checked, so the agent's event stays valid, but it is NOT kept: titles can
    # hold document names or mail subjects and nothing uses them. Keeping them needs an
    # organization setting and a retention limit first (open decision).
    if "window_title" in payload and _text(payload["window_title"], 256, allow_empty=True) is None:
        return None
    return {"process_name": name, "duration_seconds": duration}


def _activity_sample(payload):
    keys = {"state", "idle_seconds", "sample_interval_seconds"}
    if not _keys_ok(payload, keys, keys):
        return None
    idle = _number(payload["idle_seconds"], 0, MAX_IDLE_SECONDS)
    interval = _number(payload["sample_interval_seconds"], 1, MAX_SAMPLE_INTERVAL_SECONDS)
    if payload["state"] not in ("active", "idle") or idle is None or interval is None:
        return None
    return {"state": payload["state"], "idle_seconds": idle, "sample_interval_seconds": interval}


def _website_visit(payload):
    if not _keys_ok(payload, {"domain"}, {"domain", "duration_seconds"}):
        return None
    domain = domain_name(payload["domain"])
    if domain is None:
        return None
    cleaned = {"domain": domain}
    if "duration_seconds" in payload:
        duration = _number(payload["duration_seconds"], 0, MAX_DURATION_SECONDS)
        if duration is None:
            return None
        cleaned["duration_seconds"] = duration
    return cleaned


def _session_state(payload):
    if not _keys_ok(payload, {"session_type"}, {"session_type", "state"}):
        return None
    if payload["session_type"] not in SESSION_TYPES:
        return None
    cleaned = {"session_type": payload["session_type"]}
    if "state" in payload:
        if payload["state"] not in SESSION_STATES:
            return None
        cleaned["state"] = payload["state"]
    return cleaned


PAYLOAD_CHECKS = {
    "app_focus": _app_focus,
    "activity_sample": _activity_sample,
    "website_visit": _website_visit,
    "session_state": _session_state,
}


def validate_event(raw, now):
    """Return a clean event dict, or None if the event must be rejected.

    The clean event has: event_id (str), event_type, captured_at (datetime), payload (dict).
    """
    if not isinstance(raw, dict) or set(raw) != {"event_id", "event_type", "captured_at", "payload"}:
        return None
    if not is_event_id(raw["event_id"]) or raw["event_type"] not in EVENT_TYPES:
        return None
    captured = raw["captured_at"]
    if not isinstance(captured, str) or UTC_TIMESTAMP.fullmatch(captured) is None:
        return None
    try:
        captured_at = datetime.fromisoformat(captured.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
    if captured_at > now + MAX_CLOCK_AHEAD or captured_at < now - MAX_EVENT_AGE:
        return None
    payload = raw["payload"]
    if not isinstance(payload, dict):
        return None
    cleaned_payload = PAYLOAD_CHECKS[raw["event_type"]](payload)
    if cleaned_payload is None:
        return None
    return {
        "event_id": raw["event_id"],
        "event_type": raw["event_type"],
        "captured_at": captured_at,
        "payload": cleaned_payload,
    }
