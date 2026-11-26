"""The View button and the organization detail page: real counts, live refresh, and Super Admin only."""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from accounts.tests.factories import make_org, make_super_admin, make_user
from analytics.services import platform_report
from devices.tests.test_setup import make_device


class OrganizationViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.super_admin = make_super_admin("root-view@t.test")
        cls.org = make_org("Org View")
        cls.other = make_org("Org Other")
        make_user("admin1-view@t.test", Role.ADMIN, cls.org, "Admin One")
        make_user("admin2-view@t.test", Role.ADMIN, cls.org, "Admin Two")
        make_user("admin-other@t.test", Role.ADMIN, cls.other, "Other Admin")
        make_user("emp-view@t.test", Role.EMPLOYEE, cls.org, "Employee View")
        make_device(make_user("emp2-view@t.test", Role.EMPLOYEE, cls.org, "Employee Two"), last_seen=timezone.now())

    def setUp(self):
        self.client.force_login(self.super_admin)

    def test_each_organization_row_has_a_view_button(self):
        html = self.client.get(reverse("organization_list")).content.decode()
        self.assertIn(f'href="{reverse("organization_detail", args=[self.org.pk])}"', html)
        self.assertIn(f'href="{reverse("organization_detail", args=[self.other.pk])}"', html)
        self.assertIn('class="btn-sm btn-view"', html)
        self.assertIn('aria-label="View Org View"', html)

    def test_detail_shows_the_total_number_of_admins(self):
        response = self.client.get(reverse("organization_detail", args=[self.org.pk]))
        self.assertEqual(response.context["figures"]["admin_total"], 2)
        self.assertContains(response, "Admins (2)")

    def test_detail_counts_match_the_platform_overview(self):
        report = platform_report(self.super_admin)
        row = next(org for org in report["organizations"] if org.pk == self.org.pk)
        figures = self.client.get(reverse("organization_detail", args=[self.org.pk])).context["figures"]
        self.assertEqual(figures["admin_total"], row.admin_count)
        self.assertEqual(figures["employee_count"], row.employee_count)
        self.assertEqual(figures["device_count"], row.device_count)
        self.assertEqual(figures["online_count"], row.online_count)

    def test_the_figures_panel_refreshes_itself_and_shows_new_admins(self):
        response = self.client.get(reverse("organization_detail", args=[self.org.pk]))
        self.assertContains(response, f'hx-get="{reverse("organization_figures", args=[self.org.pk])}"')
        make_user("admin3-view@t.test", Role.ADMIN, self.org, "Admin Three")
        panel = self.client.get(reverse("organization_figures", args=[self.org.pk]))
        self.assertEqual(panel.context["figures"]["admin_total"], 3)
        self.assertContains(panel, "3")
        self.assertNotContains(panel, "<html")

    def test_other_organizations_admins_are_not_counted(self):
        response = self.client.get(reverse("organization_detail", args=[self.other.pk]))
        self.assertEqual(response.context["figures"]["admin_total"], 1)

    def test_only_the_super_admin_can_open_the_detail_and_panel(self):
        self.client.force_login(make_user("admin-view-x@t.test", Role.ADMIN, self.org))
        self.assertEqual(self.client.get(reverse("organization_detail", args=[self.org.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("organization_figures", args=[self.org.pk])).status_code, 403)

    def test_an_unknown_organization_is_not_found(self):
        self.assertEqual(self.client.get(reverse("organization_detail", args=[999999])).status_code, 404)
        self.assertEqual(self.client.get(reverse("organization_figures", args=[999999])).status_code, 404)


class ActivateFromDetailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.super_admin = make_super_admin("root-act@t.test")
        cls.org = make_org("Org Act")
        cls.org.is_active = False
        cls.org.save()

    def setUp(self):
        self.client.force_login(self.super_admin)
        self.url = reverse("organization_set_active", args=[self.org.pk])

    def test_activating_from_the_detail_page_stays_on_that_page(self):
        response = self.client.post(self.url, {"active": "1", "next": "detail"}, follow=True)
        self.assertRedirects(response, reverse("organization_detail", args=[self.org.pk]))
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_active)
        self.assertContains(response, "activated.")
        self.assertContains(response, "Deactivate organization")

    def test_the_list_page_still_goes_back_to_the_list(self):
        response = self.client.post(self.url, {"active": "1"})
        self.assertRedirects(response, reverse("organization_list"))

    def test_an_unexpected_next_value_is_ignored(self):
        response = self.client.post(self.url, {"active": "1", "next": "https://evil.example"})
        self.assertRedirects(response, reverse("organization_list"))
