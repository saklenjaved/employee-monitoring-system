"""The Super Admin's Admins section: every Admin across organizations, with status, search and filters."""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts.models import EndReason, Membership, MembershipStatus, Role, User
from accounts.services import open_invitation, platform_admins
from accounts.tests.factories import make_membership
from analytics import services as analytics_services
from organizations.models import Organization

PASSWORD = "a-long-test-password-1"


class AdminListTests(TestCase):
    def setUp(self):
        cache.clear()

    @classmethod
    def setUpTestData(cls):
        cls.org_a = Organization.objects.create(name="Acme Corp")
        cls.org_b = Organization.objects.create(name="Beta Ltd", is_active=False)
        cls.super_admin = User.objects.create_superuser(email="super@platform.test", password=PASSWORD, full_name="Super")
        cls.active = User.objects.create_user(
            email="alice@acme.test", password=PASSWORD, full_name="Alice Active", role=Role.ADMIN, organization=cls.org_a
        )
        make_membership(cls.active)
        cls.inactive = User.objects.create_user(
            email="ivan@acme.test", password=PASSWORD, full_name="Ivan Inactive", role=Role.ADMIN, organization=cls.org_a
        )
        make_membership(cls.inactive)
        cls.inactive.is_active = False
        cls.inactive.save()
        cls.pending, _, _ = open_invitation(cls.super_admin, cls.org_b, "pat@beta.test", "Pat Pending", Role.ADMIN)
        cls.employee = User.objects.create_user(
            email="emp@acme.test", password=PASSWORD, full_name="Erin Employee", role=Role.EMPLOYEE, organization=cls.org_a
        )
        make_membership(cls.employee)
        cls.url = reverse("admin_list")

    def get(self, **query):
        self.client.force_login(self.super_admin)
        return self.client.get(self.url, query)

    def test_only_the_super_admin_can_open_the_page(self):
        for user in (self.active, self.employee):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)  # sent to the login page

    def test_it_lists_every_admin_with_organization_status_and_date(self):
        response = self.get()
        for text in ("Alice Active", "alice@acme.test", "Ivan Inactive", "Pat Pending", "pat@beta.test", "Acme Corp", "Beta Ltd"):
            self.assertContains(response, text)
        self.assertContains(response, "Deactivated")  # Beta Ltd is a deactivated organization
        self.assertContains(response, "status ok")
        self.assertContains(response, "status wait")
        self.assertContains(response, "status bad")
        self.assertNotContains(response, "Erin Employee")  # only Admin accounts

    def test_status_is_active_pending_or_inactive(self):
        statuses = dict(platform_admins(self.super_admin).values_list("email", "admin_status"))
        self.assertEqual(
            statuses, {"alice@acme.test": "active", "ivan@acme.test": "inactive", "pat@beta.test": "pending"}
        )

    def test_an_admin_whose_membership_ended_is_inactive(self):
        Membership.objects.filter(user=self.active).update(status=MembershipStatus.ENDED, end_reason=EndReason.REMOVED)
        statuses = dict(platform_admins(self.super_admin).values_list("email", "admin_status"))
        self.assertEqual(statuses["alice@acme.test"], "inactive")

    def test_search_finds_by_name_email_or_organization(self):
        for query, expected in (("alice", "Alice Active"), ("beta.test", "Pat Pending"), ("Beta Ltd", "Pat Pending")):
            response = self.get(q=query)
            self.assertContains(response, expected)
        response = self.get(q="beta")
        self.assertNotContains(response, "Alice Active")

    def test_filter_by_status_and_by_organization(self):
        response = self.get(status="pending")
        self.assertContains(response, "Pat Pending")
        self.assertNotContains(response, "Alice Active")
        response = self.get(organization=str(self.org_a.pk))
        self.assertContains(response, "Alice Active")
        self.assertContains(response, "Ivan Inactive")
        self.assertNotContains(response, "Pat Pending")

    def test_a_pending_admin_can_be_sent_a_new_invitation_from_the_list(self):
        response = self.get()
        self.assertContains(response, reverse("organization_admin_resend_invite", args=[self.pending.pk]))
        self.assertNotContains(response, reverse("organization_admin_resend_invite", args=[self.active.pk]))

    def test_the_sidebar_links_to_the_admins_page_for_the_super_admin_only(self):
        self.assertContains(self.get(), f'href="{self.url}"')
        self.client.force_login(self.active)
        self.assertNotContains(self.client.get(reverse("dashboard_organization")), f'href="{self.url}"')

    def test_other_roles_get_no_admin_accounts_from_the_service(self):
        self.assertFalse(platform_admins(self.active).exists())
        self.assertFalse(platform_admins(self.employee).exists())


class PlatformDashboardAdminStatsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Acme Corp")
        cls.super_admin = User.objects.create_superuser(email="super@platform.test", password=PASSWORD, full_name="Super")

    def test_the_numbers_come_from_the_database_and_change_with_it(self):
        self.client.force_login(self.super_admin)
        empty = analytics_services.platform_report(self.super_admin)
        self.assertEqual((empty["admin_count"], empty["active_admin_count"], empty["pending_admin_count"]), (0, 0, 0))

        active = User.objects.create_user(
            email="a@acme.test", password=PASSWORD, full_name="Active One", role=Role.ADMIN, organization=self.org
        )
        make_membership(active)
        open_invitation(self.super_admin, self.org, "p@acme.test", "Pending One", Role.ADMIN)

        report = analytics_services.platform_report(self.super_admin)
        self.assertEqual((report["admin_count"], report["active_admin_count"], report["pending_admin_count"]), (2, 1, 1))
        self.assertEqual({admin.email for admin in report["recent_admins"]}, {"a@acme.test", "p@acme.test"})

        page = self.client.get(reverse("dashboard_platform"))
        for text in ("Total Admins", "Active Admins", "Pending invitations", "Latest Admins", "Active One", "Pending One"):
            self.assertContains(page, text)
        self.assertContains(page, f'{reverse("admin_list")}?status=pending')

    def test_the_latest_admins_list_shows_at_most_five(self):
        for number in range(7):
            open_invitation(self.super_admin, self.org, f"n{number}@acme.test", f"Name {number}", Role.ADMIN)
        report = analytics_services.platform_report(self.super_admin)
        self.assertEqual(len(report["recent_admins"]), 5)
        self.assertEqual(report["admin_count"], 7)
