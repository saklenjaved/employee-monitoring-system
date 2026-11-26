"""The Admin's live view of one employee: agent status and the latest activity, and who may see it."""
import uuid
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from analytics.services import recent_activity
from devices.tests.test_setup import make_device
from monitoring.models import EventType, MonitoringEvent


def add_event(device, event_type, payload, at):
    return MonitoringEvent.objects.create(
        organization=device.organization, user=device.user, device=device,
        event_id=uuid.uuid4(), event_type=event_type, captured_at=at, payload=payload,
    )


def add_sample(device, app, state, at, seconds=30.0):
    add_event(device, EventType.APP_FOCUS, {"process_name": app, "duration_seconds": seconds}, at)
    add_event(device, EventType.ACTIVITY_SAMPLE, {"state": state, "idle_seconds": 0.0, "sample_interval_seconds": 30.0}, at)


class RecentActivityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org, "Employee A")
        cls.device = make_device(cls.emp, last_seen=timezone.now())

    def test_the_latest_samples_come_first_with_their_state(self):
        now = timezone.now()
        add_sample(self.device, "notepad.exe", "idle", now - timedelta(minutes=2))
        add_sample(self.device, "chrome.exe", "active", now - timedelta(minutes=1))
        rows = recent_activity(self.emp)
        self.assertEqual([(r["app"], r["state"]) for r in rows], [("chrome.exe", "active"), ("notepad.exe", "idle")])

    def test_the_feed_is_limited(self):
        now = timezone.now()
        for index in range(25):
            add_sample(self.device, "app.exe", "active", now - timedelta(seconds=index * 30))
        self.assertEqual(len(recent_activity(self.emp, limit=20)), 20)

    def test_another_employees_events_are_not_included(self):
        other = make_user("o@a.test", Role.EMPLOYEE, self.org, "Other")
        add_sample(make_device(other), "secret.exe", "active", timezone.now())
        self.assertEqual(recent_activity(self.emp), [])


class EmployeeLivePanelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.manager = make_user("m@a.test", Role.MANAGER, cls.org_a)
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org_a, "Employee A")
        cls.other_emp = make_user("o@a.test", Role.EMPLOYEE, cls.org_a, "Other Person")
        cls.emp_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b, "Employee B")
        cls.device = make_device(cls.emp, last_seen=timezone.now())
        add_sample(cls.device, "chrome.exe", "active", timezone.now())

    def live(self, person):
        return self.client.get(reverse("dashboard_employee_live", args=[person.pk]))

    def test_the_admin_sees_status_and_recent_activity(self):
        self.device.focus_app = "chrome.exe"
        self.device.save()
        self.client.force_login(self.admin)
        response = self.live(self.emp)
        self.assertContains(response, "Live status")
        self.assertContains(response, "Now using")
        self.assertContains(response, "chrome.exe")
        self.assertNotContains(response, "Recent activity")  # the live card has no activity list any more
        self.assertContains(response, 'hx-trigger="every 10s"')

    def test_the_employee_page_includes_the_live_card_for_the_admin_only(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("dashboard_employee", args=[self.emp.pk])), "<h2>Activity timeline</h2>")
        self.client.force_login(self.emp)
        self.assertNotContains(self.client.get(reverse("dashboard_me")), "<h2>Activity timeline</h2>")

    def test_a_pc_with_no_data_says_so(self):
        self.client.force_login(self.admin)
        response = self.live(self.other_emp)
        self.assertContains(response, "No PC connected yet.")

    def test_another_organizations_employee_is_not_found(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.live(self.emp_b).status_code, 404)

    def test_a_manager_without_that_employee_in_their_teams_gets_not_found(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.live(self.emp).status_code, 404)

    def test_an_employee_cannot_open_the_panel(self):
        self.client.force_login(self.emp)
        self.assertEqual(self.live(self.emp).status_code, 403)

    def test_the_live_table_links_to_the_employee_page(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard_organization"))
        self.assertContains(response, reverse("dashboard_employee", args=[self.emp.pk]))
