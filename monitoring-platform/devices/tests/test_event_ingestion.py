import uuid
from datetime import datetime, timezone

from django.utils import timezone as dj_timezone

from devices.tests.test_agent_api import AgentApiTestCase
from monitoring.models import MonitoringEvent

EVENTS_URL = "/api/agent/v1/events/batch/"


def utc_stamp():
    return dj_timezone.now().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def make_event(event_type="app_focus", payload=None, **overrides):
    default_payloads = {
        "app_focus": {"process_name": "chrome.exe", "duration_seconds": 30},
        "activity_sample": {"state": "active", "idle_seconds": 1, "sample_interval_seconds": 30},
    }
    body = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "captured_at": utc_stamp(),
        "payload": payload if payload is not None else default_payloads[event_type],
    }
    body.update(overrides)
    return body


class EventIngestionTests(AgentApiTestCase):
    def setUp(self):
        super().setUp()
        self.registered = self.register()
        self.authenticate(self.registered["credential"])
        self.device = self.emp_a.devices.get()

    def send(self, events):
        return self.api.post(EVENTS_URL, {"events": events}, format="json")

    def test_events_are_stored_with_organization_and_employee_from_the_device(self):
        events = [make_event(), make_event("activity_sample")]
        response = self.send(events)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"accepted": [e["event_id"] for e in events], "duplicates": [], "rejected": []})
        stored = MonitoringEvent.objects.get(event_id=events[0]["event_id"])
        self.assertEqual((stored.organization, stored.user, stored.device), (self.org_a, self.emp_a, self.device))
        self.assertEqual(stored.event_type, "app_focus")
        self.assertEqual(stored.payload, {"process_name": "chrome.exe", "duration_seconds": 30})

    def test_sending_the_same_batch_again_stores_nothing_new(self):
        events = [make_event(), make_event("activity_sample")]
        self.send(events)
        again = self.send(events).json()
        self.assertEqual(again["accepted"], [])
        self.assertEqual(again["duplicates"], [e["event_id"] for e in events])
        self.assertEqual(MonitoringEvent.objects.count(), 2)

    def test_part_new_part_old(self):
        old, new = make_event(), make_event()
        self.send([old])
        result = self.send([old, new]).json()
        self.assertEqual(result["accepted"], [new["event_id"]])
        self.assertEqual(result["duplicates"], [old["event_id"]])
        self.assertEqual(MonitoringEvent.objects.count(), 2)

    def test_the_same_event_twice_in_one_batch_is_stored_once(self):
        event = make_event()
        result = self.send([event, event]).json()
        self.assertEqual(result["accepted"], [event["event_id"]])
        self.assertEqual(result["duplicates"], [event["event_id"]])
        self.assertEqual(MonitoringEvent.objects.count(), 1)

    def test_bad_events_are_rejected_and_good_ones_in_the_same_batch_are_kept(self):
        good = make_event()
        typed = make_event(payload={"process_name": "a.exe", "duration_seconds": 1, "typed_text": "secret"})
        with_org = make_event(organization_id=self.org_b.pk)
        with_nul = make_event(payload={"process_name": "a\x00.exe", "duration_seconds": 1})
        no_id = make_event(event_id="not-a-uuid")
        result = self.send([good, typed, with_org, with_nul, no_id]).json()
        self.assertEqual(result["accepted"], [good["event_id"]])
        self.assertEqual(result["rejected"], [typed["event_id"], with_org["event_id"], with_nul["event_id"]])
        self.assertNotIn("not-a-uuid", str(result))
        self.assertEqual(MonitoringEvent.objects.count(), 1)

    def test_typed_text_is_never_stored(self):
        self.send([make_event(payload={"process_name": "a.exe", "duration_seconds": 1, "typed_text": "hunter2"})])
        self.assertFalse(MonitoringEvent.objects.exists())

    def test_window_titles_are_accepted_but_never_stored(self):
        event = make_event(payload={"process_name": "winword.exe", "duration_seconds": 30, "window_title": "Salary review.docx"})
        self.assertEqual(self.send([event]).json()["accepted"], [event["event_id"]])
        stored = MonitoringEvent.objects.get()
        self.assertEqual(stored.payload, {"process_name": "winword.exe", "duration_seconds": 30})
        self.assertNotIn("Salary", str(list(MonitoringEvent.objects.values_list("payload", flat=True))))

    def test_names_that_carry_paths_or_urls_are_rejected(self):
        path = make_event(payload={"process_name": "C:\\Users\\Alice\\notes.exe", "duration_seconds": 1})
        url = make_event("website_visit", {"domain": "http://example.com/inbox?subject=hello"})
        good = make_event()
        result = self.send([path, url, good]).json()
        self.assertEqual(result["rejected"], [path["event_id"], url["event_id"]])
        self.assertEqual(result["accepted"], [good["event_id"]])

    def test_event_batches_are_limited_per_device(self):
        for _ in range(60):
            self.assertEqual(self.send([make_event()]).status_code, 200)
        limited = self.send([make_event()])
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["code"], "rate_limited")
        # Another device has its own allowance.
        other = self.register(self.emp_b)
        self.authenticate(other["credential"])
        self.assertEqual(self.send([make_event()]).status_code, 200)

    def test_a_content_length_that_is_not_a_number_is_a_400_not_a_crash(self):
        response = self.api.post(EVENTS_URL, {"events": [make_event()]}, format="json", CONTENT_LENGTH="abc")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_request")

    def test_events_from_two_devices_do_not_interfere(self):
        shared = make_event()
        other = self.register(self.emp_b)
        self.send([shared])
        self.authenticate(other["credential"])
        result = self.send([shared]).json()
        # Another device's events are invisible: this one is new for the second device.
        self.assertEqual(result["accepted"], [shared["event_id"]])
        self.assertEqual(MonitoringEvent.objects.filter(event_id=shared["event_id"]).count(), 2)
        owners = set(MonitoringEvent.objects.filter(event_id=shared["event_id"]).values_list("user_id", "organization_id"))
        self.assertEqual(owners, {(self.emp_a.pk, self.org_a.pk), (self.emp_b.pk, self.org_b.pk)})

    def test_bad_batches_are_refused(self):
        for body in ({}, {"events": []}, {"events": "x"}, {"events": [make_event()], "organization_id": 1}, [make_event()]):
            with self.subTest(body=str(body)[:40]):
                response = self.api.post(EVENTS_URL, body, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "invalid_request")
        self.assertFalse(MonitoringEvent.objects.exists())

    def test_batch_over_500_events_is_refused(self):
        response = self.send([make_event() for _ in range(501)])
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["code"], "payload_too_large")
        self.assertFalse(MonitoringEvent.objects.exists())

    def test_batch_of_500_events_is_accepted(self):
        response = self.send([make_event() for _ in range(500)])
        self.assertEqual(len(response.json()["accepted"]), 500)
        self.assertEqual(MonitoringEvent.objects.count(), 500)

    def test_a_credential_is_required(self):
        self.api.credentials()
        self.assertEqual(self.send([make_event()]).status_code, 401)
        self.assertFalse(MonitoringEvent.objects.exists())

    def test_a_revoked_device_cannot_send_events(self):
        self.device.is_active = False
        self.device.save()
        self.assertEqual(self.send([make_event()]).status_code, 401)
        self.assertFalse(MonitoringEvent.objects.exists())

    def test_a_deactivated_employee_cannot_send_events(self):
        self.emp_a.is_active = False
        self.emp_a.save()
        self.assertEqual(self.send([make_event()]).status_code, 403)
        self.assertFalse(MonitoringEvent.objects.exists())

    def test_old_events_from_the_offline_queue_are_accepted(self):
        two_days_ago = datetime.now(timezone.utc).timestamp() - 2 * 86400
        stamp = datetime.fromtimestamp(two_days_ago, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        result = self.send([make_event(captured_at=stamp)]).json()
        self.assertEqual(len(result["accepted"]), 1)
