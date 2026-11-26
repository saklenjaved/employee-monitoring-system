"""An Admin's view of one employee's monitoring: timeline, attendance, date filter, search and access limits."""
from datetime import datetime, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from analytics.services import activity_timeline, attendance_by_day, display_zone
from analytics.tests.test_employee_live import add_sample
from devices.tests.test_setup import make_device
from screenshots.tests.test_pages import make_shot


def today_in_display_zone():
    return timezone.now().astimezone(display_zone()).date()


def at_local(day, hour, minute):
    """An aware datetime for a local clock time on `day`, in display time."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=display_zone())


class EmployeeTimelineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org, "Employee A")
        cls.other = make_user("o@a.test", Role.EMPLOYEE, cls.org, "Other")
        cls.device = make_device(cls.emp, last_seen=timezone.now())
        cls.other_device = make_device(cls.other, last_seen=timezone.now())

    def test_the_timeline_only_holds_this_employees_applications(self):
        today = today_in_display_zone()
        add_sample(self.device, "chrome.exe", "active", at_local(today, 10, 0))
        add_sample(self.other_device, "secret.exe", "active", at_local(today, 10, 5))
        apps = [row["app"] for row in activity_timeline(self.emp, today, today)]
        self.assertEqual(apps, ["chrome.exe"])

    def test_the_timeline_respects_the_chosen_dates(self):
        today = today_in_display_zone()
        three_days_ago = today - timedelta(days=3)
        add_sample(self.device, "old.exe", "active", at_local(three_days_ago, 9, 0))
        add_sample(self.device, "new.exe", "active", at_local(today, 9, 0))
        apps = [row["app"] for row in activity_timeline(self.emp, today, today)]
        self.assertEqual(apps, ["new.exe"])

    def test_the_timeline_is_newest_first(self):
        today = today_in_display_zone()
        add_sample(self.device, "first.exe", "active", at_local(today, 9, 0))
        add_sample(self.device, "second.exe", "active", at_local(today, 11, 0))
        apps = [row["app"] for row in activity_timeline(self.emp, today, today)]
        self.assertEqual(apps, ["second.exe", "first.exe"])


class AttendanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org, "Employee A")
        cls.device = make_device(cls.emp, last_seen=timezone.now())

    def test_first_and_last_activity_are_found_for_each_day(self):
        today = today_in_display_zone()
        add_sample(self.device, "a.exe", "active", at_local(today, 9, 15))
        add_sample(self.device, "b.exe", "active", at_local(today, 17, 40))
        rows = attendance_by_day(self.emp, today, today)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["first"].strftime("%H:%M"), "09:15")
        self.assertEqual(rows[0]["last"].strftime("%H:%M"), "17:40")


class EmployeeDetailPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.other_org = make_org("Org B")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.other_admin = make_user("admin@b.test", Role.ADMIN, cls.other_org)
        cls.employee = make_user("e@a.test", Role.EMPLOYEE, cls.org, "Employee A")
        cls.device = make_device(cls.employee, last_seen=timezone.now())
        today = today_in_display_zone()
        add_sample(cls.device, "visible-app.exe", "active", at_local(today, 10, 0))

    def test_the_admin_sees_the_timeline_and_attendance(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard_employee", args=[self.employee.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activity timeline")
        self.assertContains(response, "Attendance")
        self.assertContains(response, "visible-app.exe")

    def test_an_admin_of_another_organization_gets_not_found(self):
        self.client.force_login(self.other_admin)
        response = self.client.get(reverse("dashboard_employee", args=[self.employee.pk]))
        self.assertEqual(response.status_code, 404)

    def test_an_employee_cannot_open_another_employees_detail(self):
        other = make_user("o@a.test", Role.EMPLOYEE, self.org, "Other")
        self.client.force_login(other)
        response = self.client.get(reverse("dashboard_employee", args=[self.employee.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "visible-app.exe", status_code=403)

    def test_the_detail_page_links_back_to_the_employee_list(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard_employee", args=[self.employee.pk]))
        self.assertContains(response, reverse("dashboard_employees"))


class EmployeeIndexTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.other_org = make_org("Org B")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.ayesha = make_user("ayesha@a.test", Role.EMPLOYEE, cls.org, "Ayesha Khan")
        cls.rahul = make_user("rahul@a.test", Role.EMPLOYEE, cls.org, "Rahul Mehta")
        cls.outsider = make_user("ayesha@b.test", Role.EMPLOYEE, cls.other_org, "Ayesha Other Org")

    def test_the_list_only_holds_the_admins_organization(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard_employees"))
        self.assertContains(response, "Ayesha Khan")
        self.assertNotContains(response, "Ayesha Other Org")

    def test_a_search_narrows_the_list_by_name(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard_employees"), {"q": "rahul"})
        self.assertContains(response, "Rahul Mehta")
        self.assertNotContains(response, "Ayesha Khan")

    def test_an_employee_cannot_use_the_list(self):
        self.client.force_login(self.ayesha)
        response = self.client.get(reverse("dashboard_employees"))
        self.assertNotEqual(response.status_code, 200)


class OnlyThisEmployeeTests(TestCase):
    """Two employees in one organization. The page for one shows nothing of the other."""

    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.alice = make_user("alice@a.test", Role.EMPLOYEE, cls.org, "Alice Only")
        cls.bob = make_user("bob@a.test", Role.EMPLOYEE, cls.org, "Bob Other")
        cls.empty = make_user("empty@a.test", Role.EMPLOYEE, cls.org, "Nobody Recorded")
        cls.alice_device = make_device(cls.alice, last_seen=timezone.now())
        cls.bob_device = make_device(cls.bob, last_seen=timezone.now())
        today = today_in_display_zone()
        add_sample(cls.alice_device, "alice-app.exe", "active", at_local(today, 10, 0))
        add_sample(cls.bob_device, "bob-app.exe", "active", at_local(today, 10, 0))
        cls.alice_shot = make_shot(cls.alice, cls.alice_device, at_local(today, 11, 0))
        cls.bob_shot = make_shot(cls.bob, cls.bob_device, at_local(today, 11, 0))

    def page(self, employee, **params):
        self.client.force_login(self.admin)
        return self.client.get(reverse("dashboard_employee", args=[employee.pk]), params)

    def test_alices_page_has_no_bob_data(self):
        html = self.page(self.alice).content.decode()
        self.assertIn("alice-app.exe", html)
        self.assertNotIn("bob-app.exe", html)
        self.assertNotIn("bob@a.test", html)
        self.assertNotIn("Bob Other", html)

    def test_alices_screenshots_are_only_hers(self):
        html = self.page(self.alice).content.decode()
        self.assertIn(reverse("screenshot_detail", args=[self.alice_shot.pk]), html)
        self.assertNotIn(reverse("screenshot_detail", args=[self.bob_shot.pk]), html)

    def test_the_trend_chart_data_is_only_this_employee_and_the_selected_dates(self):
        self.client.force_login(self.admin)
        today = today_in_display_zone().isoformat()
        response = self.client.get(reverse("trend_employee", args=[self.alice.pk]), {"start": today, "end": today})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["labels"], [today])  # exactly the selected dates, no widening

    def test_the_screenshot_tab_counts_every_screenshot_in_the_range(self):
        today = today_in_display_zone()
        for minute in range(1, 16):  # 15 more, so the page shows 12 of 16
            make_shot(self.alice, self.alice_device, at_local(today, 12, minute))
        html = self.page(self.alice, start=today.isoformat(), end=today.isoformat()).content.decode()
        self.assertIn("Showing the latest 12 of 16.", html)

    def test_an_employee_with_no_data_shows_empty_states_only(self):
        response = self.page(self.empty)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertContains(response, "No activity recorded in these dates.")
        self.assertNotIn("alice-app.exe", html)
        self.assertNotIn("bob-app.exe", html)
        self.assertNotIn(reverse("screenshot_detail", args=[self.alice_shot.pk]), html)


class EmployeeNavigationTests(TestCase):
    """Screenshots and Profile stay inside the selected employee's context, with a way back."""

    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org N")
        cls.other_org = make_org("Org M")
        cls.admin = make_user("admin@n.test", Role.ADMIN, cls.org)
        cls.alice = make_user("alice@n.test", Role.EMPLOYEE, cls.org, "Alice Nav")
        cls.bob = make_user("bob@n.test", Role.EMPLOYEE, cls.org, "Bob Nav")
        cls.outsider = make_user("out@m.test", Role.EMPLOYEE, cls.other_org, "Outsider")
        cls.alice_device = make_device(cls.alice, last_seen=timezone.now())
        cls.bob_device = make_device(cls.bob, last_seen=timezone.now())
        today = today_in_display_zone()
        cls.alice_shot = make_shot(cls.alice, cls.alice_device, at_local(today, 11, 0))
        cls.bob_shot = make_shot(cls.bob, cls.bob_device, at_local(today, 11, 0))

    def setUp(self):
        self.client.force_login(self.admin)

    def test_the_monitoring_page_links_to_the_employees_screenshots_and_profile_with_dates(self):
        response = self.client.get(
            reverse("dashboard_employee", args=[self.alice.pk]), {"start": "2026-11-20", "end": "2026-11-26"}
        )
        self.assertContains(response, f"{reverse('screenshot_list')}?employee={self.alice.pk}&amp;start=2026-11-20&amp;end=2026-11-26")
        self.assertContains(response, f"{reverse('person_detail', args=[self.alice.pk])}?from=monitoring&amp;start=2026-11-20&amp;end=2026-11-26")

    def test_the_screenshots_page_shows_only_that_employee_with_a_breadcrumb_back(self):
        today = today_in_display_zone().isoformat()
        response = self.client.get(reverse("screenshot_list"), {"employee": self.alice.pk, "start": today, "end": today})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("screenshot_detail", args=[self.alice_shot.pk]))
        self.assertNotContains(response, reverse("screenshot_detail", args=[self.bob_shot.pk]))
        back = reverse("dashboard_employee", args=[self.alice.pk]) + f"?start={today}&amp;end={today}"
        self.assertContains(response, f'<a href="{back}">Alice Nav</a>')
        self.assertContains(response, "Screenshots</span>")
        self.assertContains(response, 'href="/dashboard/" aria-current="page" title="Dashboard"')
        self.assertNotContains(response, 'aria-current="page" title="Screenshots"')

    def test_an_employee_outside_the_viewers_organization_falls_back_to_the_normal_list(self):
        response = self.client.get(reverse("screenshot_list"), {"employee": self.outsider.pk})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Outsider")
        self.assertNotContains(response, f"{reverse('dashboard_employee', args=[self.outsider.pk])}")

    def test_the_profile_page_keeps_the_monitoring_breadcrumb(self):
        response = self.client.get(reverse("person_detail", args=[self.alice.pk]), {"from": "monitoring"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'<a href="{reverse("dashboard_employee", args=[self.alice.pk])}">Alice Nav</a>')
        self.assertContains(response, 'aria-current="page" title="Dashboard"')
        self.assertNotContains(response, 'aria-current="page" title="People"')

    def test_the_profile_page_without_the_monitoring_context_keeps_the_people_breadcrumb(self):
        response = self.client.get(reverse("person_detail", args=[self.alice.pk]))
        self.assertContains(response, f'href="{reverse("person_list")}"')
        self.assertNotContains(response, f'href="{reverse("dashboard_employee", args=[self.alice.pk])}"')


class EmployeeScreenshotScopeTests(TestCase):
    """An employee's Screenshots page can show only that employee, and no global control can change that."""

    @classmethod
    def setUpTestData(cls):
        from screenshots.tests.test_pages import make_shot

        cls.org = make_org("Org S")
        cls.admin = make_user("admin@s.test", Role.ADMIN, cls.org)
        cls.alice = make_user("alice@s.test", Role.EMPLOYEE, cls.org, "Alice Scope")
        cls.bob = make_user("bob@s.test", Role.EMPLOYEE, cls.org, "Bob Scope")
        alice_device = make_device(cls.alice, last_seen=timezone.now())
        bob_device = make_device(cls.bob, last_seen=timezone.now())
        today = today_in_display_zone()
        cls.alice_shot = make_shot(cls.alice, alice_device, at_local(today, 11, 0))
        cls.bob_shot = make_shot(cls.bob, bob_device, at_local(today, 11, 0))

    def setUp(self):
        self.client.force_login(self.admin)

    def test_the_employee_page_hides_the_global_filters(self):
        html = self.client.get(reverse("screenshot_list"), {"employee": self.alice.pk}).content.decode()
        self.assertNotIn('id="shot-q"', html)
        self.assertNotIn('name="team"', html)
        self.assertNotIn('name="department"', html)
        self.assertNotIn('name="status"', html)
        self.assertNotIn("Search employee", html)
        self.assertIn(f'<input type="hidden" name="employee" value="{self.alice.pk}">', html)
        self.assertIn("Alice Scope", html)
        self.assertNotIn("Open screenshot settings", html)

    def test_only_that_employees_screenshots_are_listed(self):
        html = self.client.get(reverse("screenshot_list"), {"employee": self.alice.pk}).content.decode()
        self.assertIn(reverse("screenshot_detail", args=[self.alice_shot.pk]), html)
        self.assertNotIn(reverse("screenshot_detail", args=[self.bob_shot.pk]), html)

    def test_other_filters_cannot_widen_or_switch_the_employee_page(self):
        response = self.client.get(
            reverse("screenshot_list"), {"employee": self.alice.pk, "q": "Bob", "team": "", "status": "", "department": ""}
        )
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(reverse("screenshot_detail", args=[self.bob_shot.pk]), html)
        self.assertIn(reverse("screenshot_detail", args=[self.alice_shot.pk]), html)

    def test_the_global_page_keeps_its_filters(self):
        html = self.client.get(reverse("screenshot_list")).content.decode()
        self.assertIn('id="shot-q"', html)
        self.assertIn('name="employee"', html)
