"""The audit log page: Admins see their organization only, the Super Admin sees everything."""
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_super_admin, make_user
from audit.services import record_event


def actions_on_page(response):
    return [entry.action for entry in response.context["listing"]["page"]]


class AuditLogPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.manager_a = make_user("manager@a.test", Role.MANAGER, cls.org_a)
        cls.employee_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a)
        cls.admin_b = make_user("admin@b.test", Role.ADMIN, cls.org_b)
        cls.super_admin = make_super_admin()
        # Created in this order, so the newest entry is the last one below.
        record_event(cls.super_admin, "organization.created", organization=cls.org_a, target=cls.org_a)
        record_event(cls.admin_a, "user.name_changed", organization=cls.org_a, detail="from 'Ann' to 'Anne'")
        record_event(cls.admin_b, "classification_rule.created", organization=cls.org_b, detail="app rival.exe -> neutral")
        record_event(cls.super_admin, "platform.setting_changed")
        cls.url = reverse("audit_log_list")

    def get(self, query_string="", user=None):
        self.client.force_login(user or self.admin_a)
        return self.client.get(f"{self.url}?{query_string}")

    def test_an_admin_sees_only_their_organizations_entries_newest_first(self):
        self.assertEqual(actions_on_page(self.get()), ["user.name_changed", "organization.created"])

    def test_an_admin_never_sees_another_organization_or_platform_entries(self):
        response = self.get("q=rival")
        self.assertEqual(actions_on_page(response), [])
        self.assertNotContains(response, "rival.exe")
        self.assertContains(response, "No entries match your search or filters.")
        self.assertNotContains(self.get(), "platform.setting_changed")

    def test_an_admin_can_search_their_entries_by_detail(self):
        self.assertEqual(actions_on_page(self.get("q=anne")), ["user.name_changed"])

    def test_an_admin_action_choices_only_come_from_their_entries(self):
        choices = [value for value, _ in self.get().context["listing"]["filters"][0]["choices"]]
        self.assertEqual(choices, ["organization.created", "user.name_changed"])

    def test_the_super_admin_sees_every_entry_newest_first_with_its_organization(self):
        response = self.get(user=self.super_admin)
        self.assertEqual(
            actions_on_page(response),
            ["platform.setting_changed", "classification_rule.created", "user.name_changed", "organization.created"],
        )
        self.assertContains(response, "Platform")
        self.assertContains(response, "Org B")

    def test_the_super_admin_can_narrow_to_one_organization(self):
        response = self.get(f"organization={self.org_b.pk}", user=self.super_admin)
        self.assertEqual(actions_on_page(response), ["classification_rule.created"])

    def test_the_super_admin_can_filter_by_action(self):
        response = self.get("action=organization.created", user=self.super_admin)
        self.assertEqual(actions_on_page(response), ["organization.created"])

    def test_an_empty_organization_says_there_are_no_entries_yet(self):
        empty_admin = make_user("admin@empty.test", Role.ADMIN, make_org("Empty"))
        self.client.force_login(empty_admin)
        self.assertContains(self.client.get(self.url), "No audit entries yet.")

    def test_managers_and_employees_cannot_open_the_page(self):
        for user in (self.manager_a, self.employee_a):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_a_signed_out_visitor_is_sent_to_sign_in(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class AuditTimeAndFilterTests(TestCase):
    """Times are stored in UTC and shown in India time, converted once. Filters and pages work together."""

    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.url = reverse("audit_log_list")

    def get(self, query_string=""):
        self.client.force_login(self.admin)
        return self.client.get(f"{self.url}?{query_string}")

    def today_ist(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Kolkata")).date()

    def test_a_utc_time_is_shown_as_indian_time_once(self):
        from datetime import datetime, timezone

        from audit.models import AuditLog
        from audit.views import _show

        entry = AuditLog(action="monitoring.started", created_at=datetime(2026, 11, 19, 9, 15, tzinfo=timezone.utc))
        shown = _show(entry)
        self.assertEqual(shown.local_date, "19 Nov 2026")
        self.assertEqual(shown.local_time, "02:45 PM")  # 09:15 UTC is 14:45 in India
        self.assertEqual(entry.created_at.hour, 9)  # the stored value is not changed

    def test_the_page_shows_the_same_time_on_every_refresh(self):
        record_event(self.admin, "user.name_changed", organization=self.org, detail="x")
        first = self.get().context["listing"]["page"][0]
        second = self.get().context["listing"]["page"][0]
        self.assertEqual((first.local_date, first.local_time), (second.local_date, second.local_time))

    def test_the_date_range_includes_today_and_excludes_tomorrow(self):
        from datetime import timedelta

        record_event(self.admin, "user.name_changed", organization=self.org, detail="today")
        today = self.today_ist().isoformat()
        tomorrow = (self.today_ist() + timedelta(days=1)).isoformat()
        self.assertEqual(len(self.get(f"from={today}&to={today}").context["listing"]["page"]), 1)
        self.assertEqual(len(self.get(f"from={tomorrow}").context["listing"]["page"]), 0)

    def test_an_invalid_or_backwards_date_range_says_so(self):
        response = self.get("from=not-a-date")
        self.assertContains(response, "Enter the dates as YYYY-MM-DD.")
        response = self.get("from=2026-11-20&to=2026-11-19")
        self.assertContains(response, "The From date must be on or before the To date.")

    def test_the_status_filter_shows_only_that_status(self):
        record_event(self.admin, "monitoring.started", organization=self.org)
        record_event(self.admin, "user.name_changed", organization=self.org, detail="x")
        self.assertEqual(actions_on_page(self.get("status=Active")), ["monitoring.started"])
        self.assertEqual(actions_on_page(self.get("status=Recorded")), ["user.name_changed"])

    def test_pages_keep_the_date_range(self):
        from datetime import timedelta

        for index in range(26):
            record_event(self.admin, "user.name_changed", organization=self.org, detail=f"n{index}")
        today = self.today_ist().isoformat()
        response = self.get(f"from={today}")
        self.assertContains(response, f"from={today}&amp;page=2")
        second = self.get(f"from={today}&page=2")
        self.assertEqual(len(second.context["listing"]["page"]), 1)
        self.assertEqual(len(self.get(f"from={(self.today_ist() + timedelta(days=1)).isoformat()}").context["listing"]["page"]), 0)

    def test_summary_cards_count_the_whole_visible_log(self):
        record_event(self.admin, "user.name_changed", organization=self.org, detail="x")
        record_event(self.admin, "device.revoked", organization=self.org)
        summary = self.get("q=nothing-matches").context["summary"]
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["today"], 2)
        self.assertEqual(summary["users"], 2)
        self.assertEqual(summary["security"], 1)
