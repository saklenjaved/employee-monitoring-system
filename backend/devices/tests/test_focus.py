"""The focused window from the heartbeat, and the Admin's live employee table that shows it."""
from datetime import timedelta

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from analytics.services import live_employee_rows
from devices.serializers import HeartbeatSerializer
from devices.services import record_heartbeat, sync_state
from devices.tests.test_setup import make_device

HEARTBEAT = {"agent_version": "0.1.0", "session_type": "console", "queue_depth": 0, "last_error_code": ""}


class FocusStorageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org)

    def test_a_heartbeat_stores_the_focused_app_and_title(self):
        device = make_device(self.emp)
        record_heartbeat(device, {**HEARTBEAT, "focus_app": "notepad.exe", "focus_title": "Budget.txt"})
        device.refresh_from_db()
        self.assertEqual((device.focus_app, device.focus_title), ("notepad.exe", "Budget.txt"))
        self.assertIsNotNone(device.focus_updated_at)

    def test_a_heartbeat_without_focus_clears_the_old_focus(self):
        device = make_device(self.emp)
        record_heartbeat(device, {**HEARTBEAT, "focus_app": "notepad.exe", "focus_title": "Budget.txt"})
        record_heartbeat(device, dict(HEARTBEAT))
        device.refresh_from_db()
        self.assertEqual((device.focus_app, device.focus_title, device.focus_updated_at), ("", "", None))

    def test_the_heartbeat_schema_accepts_the_focus_fields(self):
        serializer = HeartbeatSerializer(
            data={
                "agent_version": "0.1.0",
                "sent_at": timezone.now().isoformat(),
                "session_type": "console",
                "queue_depth": 0,
                "monitoring_state": "active",
                "focus_app": "notepad.exe",
                "focus_title": "",
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["focus_app"], "notepad.exe")


class LiveRowsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.org_b = make_org("Org B")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.online = make_user("online@a.test", Role.EMPLOYEE, cls.org, "Online Person")
        cls.quiet = make_user("quiet@a.test", Role.EMPLOYEE, cls.org, "Quiet Person")
        cls.none = make_user("none@a.test", Role.EMPLOYEE, cls.org, "No Pc")
        cls.other = make_user("other@b.test", Role.EMPLOYEE, cls.org_b, "Other Org")

    def rows(self):
        return {row["name"]: row for row in live_employee_rows(self.admin)}

    def test_an_online_pc_shows_its_focus_and_queue(self):
        device = make_device(self.online, last_seen=timezone.now())
        device.focus_app, device.focus_title, device.queue_depth = "notepad.exe", "Budget.txt", 3
        device.save()
        row = self.rows()["Online Person"]
        self.assertEqual(row["state"], "uploading")
        self.assertEqual((row["focus_app"], row["focus_title"], row["queue_depth"]), ("notepad.exe", "Budget.txt", 3))

    def test_an_offline_pc_hides_its_last_focus(self):
        last_seen = timezone.now() - timedelta(seconds=settings.DEVICE_ONLINE_SECONDS + 60)
        device = make_device(self.quiet, last_seen=last_seen)
        device.focus_app = "old.exe"
        device.save()
        row = self.rows()["Quiet Person"]
        self.assertEqual((row["state"], row["focus_app"]), ("offline", ""))

    def test_an_employee_without_a_pc_is_not_connected(self):
        self.assertEqual(self.rows()["No Pc"]["state"], "not_connected")

    def test_other_organizations_are_not_listed(self):
        self.assertNotIn("Other Org", self.rows())

    def test_only_admins_get_the_live_table(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard_organization"))
        self.assertContains(response, "Current window")
        self.assertContains(response, "Online Person")

    def test_sync_state_is_the_same_rule_for_both_views(self):
        device = make_device(self.none, last_seen=timezone.now())
        self.assertEqual(sync_state(device), "up_to_date")
