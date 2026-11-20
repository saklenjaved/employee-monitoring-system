import copy
import uuid
from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase

from monitoring.validation import validate_event

NOW = datetime(2026, 11, 2, 10, 0, 0, tzinfo=timezone.utc)


def event(event_type="app_focus", payload=None, **overrides):
    payloads = {
        "app_focus": {"process_name": "chrome.exe", "duration_seconds": 30},
        "activity_sample": {"state": "active", "idle_seconds": 2, "sample_interval_seconds": 30},
        "website_visit": {"domain": "example.com", "duration_seconds": 12},
        "session_state": {"session_type": "console", "state": "monitoring"},
    }
    body = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "captured_at": "2026-11-02T09:59:00.123456Z",
        "payload": payload if payload is not None else payloads[event_type],
    }
    body.update(overrides)
    return body


class ValidEventTests(SimpleTestCase):
    def test_each_event_type_is_accepted(self):
        for event_type in ("app_focus", "activity_sample", "website_visit", "session_state"):
            with self.subTest(event_type=event_type):
                raw = event(event_type)
                clean = validate_event(raw, NOW)
                self.assertEqual(clean["event_id"], raw["event_id"])
                self.assertEqual(clean["event_type"], event_type)
                self.assertEqual(clean["payload"], raw["payload"])
                self.assertEqual(clean["captured_at"].tzinfo, timezone.utc)

    def test_timestamp_with_and_without_fraction(self):
        for stamp in ("2026-11-02T09:59:00Z", "2026-11-02T09:59:00.5Z"):
            self.assertIsNotNone(validate_event(event(captured_at=stamp), NOW))

    def test_optional_fields_may_be_left_out(self):
        self.assertIsNotNone(validate_event(event("website_visit", {"domain": "example.com"}), NOW))
        self.assertIsNotNone(validate_event(event("session_state", {"session_type": "rdp"}), NOW))

    def test_window_title_is_checked_but_not_kept(self):
        payload = {"process_name": "code.exe", "duration_seconds": 5, "window_title": "Secret plans.docx"}
        clean = validate_event(event(payload=payload), NOW)
        self.assertEqual(clean["payload"], {"process_name": "code.exe", "duration_seconds": 5})
        self.assertNotIn("Secret", str(clean))
        self.assertIsNone(validate_event(event(payload={**payload, "window_title": "t" * 257}), NOW))
        self.assertIsNone(validate_event(event(payload={**payload, "window_title": 5}), NOW))

    def test_names_are_plain_file_names_and_lower_case_domains(self):
        for good in ("chrome.exe", "Code - Insiders.exe", "System Idle Process", "7zFM.exe"):
            self.assertIsNotNone(validate_event(event(payload={"process_name": good, "duration_seconds": 1}), NOW), good)
        clean = validate_event(event("website_visit", {"domain": "Mail.Example.COM"}), NOW)
        self.assertEqual(clean["payload"]["domain"], "mail.example.com")


class InvalidEventTests(SimpleTestCase):
    def reject(self, raw):
        self.assertIsNone(validate_event(raw, NOW), raw)

    def test_not_an_object_or_missing_keys(self):
        for raw in ("text", 5, None, [], {}):
            self.reject(raw)
        for key in ("event_id", "event_type", "captured_at", "payload"):
            raw = event()
            del raw[key]
            self.reject(raw)

    def test_extra_top_level_keys_are_refused(self):
        for key in ("organization_id", "employee_id", "typed_text"):
            self.reject(event(**{key: "x"}))

    def test_extra_payload_keys_are_refused(self):
        for event_type in ("app_focus", "activity_sample", "website_visit", "session_state"):
            raw = event(event_type)
            raw["payload"] = {**raw["payload"], "typed_text": "hunter2"}
            self.reject(raw)

    def test_missing_required_payload_keys(self):
        self.reject(event(payload={"process_name": "a.exe"}))
        self.reject(event("activity_sample", {"state": "active"}))
        self.reject(event("website_visit", {"duration_seconds": 1}))
        self.reject(event("session_state", {"state": "monitoring"}))

    def test_event_id_must_be_a_lower_case_uuid4(self):
        for bad in ("not-a-uuid", str(uuid.uuid1()), str(uuid.uuid4()).upper(), "", 5, None):
            self.reject(event(event_id=bad))

    def test_event_type_must_be_known(self):
        self.reject(event(event_type="keystroke", payload={"keys": "abc"}))

    def test_timestamp_must_be_utc_with_z(self):
        for bad in ("2026-11-02T09:59:00", "2026-11-02T09:59:00+02:00", "2026-11-02 09:59:00Z", "yesterday", 123, None):
            self.reject(event(captured_at=bad))
        self.reject(event(captured_at="2026-13-45T25:61:61Z"))

    def test_timestamp_must_not_be_far_in_the_future_or_the_past(self):
        future = (NOW + timedelta(minutes=11)).strftime("%Y-%m-%dT%H:%M:%SZ")
        old = (NOW - timedelta(days=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.reject(event(captured_at=future))
        self.reject(event(captured_at=old))
        near_future = (NOW + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertIsNotNone(validate_event(event(captured_at=near_future), NOW))

    def test_numbers_must_be_real_numbers_within_limits(self):
        for bad in (-1, True, "30", None, float("nan"), float("inf"), 60 * 60 + 1):
            self.reject(event(payload={"process_name": "a.exe", "duration_seconds": bad}))
        self.reject(event("activity_sample", {"state": "idle", "idle_seconds": 0, "sample_interval_seconds": 0}))
        self.reject(event("activity_sample", {"state": "idle", "idle_seconds": -1, "sample_interval_seconds": 30}))

    def test_choices_are_checked(self):
        self.reject(event("activity_sample", {"state": "asleep", "idle_seconds": 1, "sample_interval_seconds": 30}))
        self.reject(event("session_state", {"session_type": "mainframe"}))
        self.reject(event("session_state", {"session_type": "console", "state": "napping"}))

    def test_text_limits(self):
        self.reject(event(payload={"process_name": "", "duration_seconds": 1}))
        self.reject(event(payload={"process_name": "a" * 261, "duration_seconds": 1}))
        self.reject(event(payload={"process_name": "a.exe", "duration_seconds": 1, "window_title": "t" * 257}))
        self.reject(event("website_visit", {"domain": "d" * 254}))
        self.reject(event(payload={"process_name": 7, "duration_seconds": 1}))

    def test_names_that_are_not_plain_file_names_or_domains_are_refused(self):
        for bad in (
            "C:\\Users\\Alice\\notes.exe",
            "dir/tool.exe",
            "tool.exe ",
            " tool.exe",
            "a\tb.exe",
            "evil<name>.exe",
            "name:stream",
            "   ",
        ):
            self.reject(event(payload={"process_name": bad, "duration_seconds": 1}))
        for bad in (
            "http://example.com/path",
            "example.com/path",
            "exa mple.com",
            "example.com?q=secret words",
            "user@example.com",
            "example..com",
            ".example.com",
            "",
        ):
            self.reject(event("website_visit", {"domain": bad}))

    def test_text_the_database_cannot_store_is_refused(self):
        self.reject(event(payload={"process_name": "bad\x00name.exe", "duration_seconds": 1}))
        self.reject(event(payload={"process_name": "a.exe", "duration_seconds": 1, "window_title": "x\ud800y"}))

    def test_validation_does_not_change_the_input(self):
        raw = event()
        before = copy.deepcopy(raw)
        validate_event(raw, NOW)
        self.assertEqual(raw, before)
