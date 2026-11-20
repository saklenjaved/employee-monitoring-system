from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Role, User
from accounts.services import get_visible_user, visible_users
from organizations.models import Organization


def make_user(email, role, organization):
    return User.objects.create_user(
        email=email, password="a-long-test-password-1", full_name=email, role=role, organization=organization
    )


class TenantIsolationTests(TestCase):
    """One organization must never see another organization's users."""

    @classmethod
    def setUpTestData(cls):
        cls.org_a = Organization.objects.create(name="Org A")
        cls.org_b = Organization.objects.create(name="Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.manager_a = make_user("manager@a.test", Role.MANAGER, cls.org_a)
        cls.employee_a = make_user("employee@a.test", Role.EMPLOYEE, cls.org_a)
        cls.admin_b = make_user("admin@b.test", Role.ADMIN, cls.org_b)
        cls.employee_b = make_user("employee@b.test", Role.EMPLOYEE, cls.org_b)
        cls.super_admin = User.objects.create_superuser(
            email="super@platform.test", password="a-long-test-password-1", full_name="Super"
        )

    def test_admin_sees_only_own_organization(self):
        users = set(visible_users(self.admin_a))
        self.assertEqual(users, {self.admin_a, self.manager_a, self.employee_a})

    def test_admin_cannot_look_up_other_organization_user(self):
        self.assertIsNone(get_visible_user(self.admin_a, self.employee_b.pk))
        self.assertIsNone(get_visible_user(self.admin_a, self.admin_b.pk))

    def test_admin_can_look_up_own_organization_user(self):
        self.assertEqual(get_visible_user(self.admin_a, self.employee_a.pk), self.employee_a)

    def test_employee_sees_only_self(self):
        self.assertEqual(list(visible_users(self.employee_a)), [self.employee_a])
        self.assertIsNone(get_visible_user(self.employee_a, self.manager_a.pk))

    def test_manager_sees_only_self_until_teams_exist(self):
        self.assertEqual(list(visible_users(self.manager_a)), [self.manager_a])

    def test_super_admin_sees_admin_accounts_but_not_employees(self):
        users = set(visible_users(self.super_admin))
        self.assertEqual(users, {self.admin_a, self.admin_b})
        self.assertIsNone(get_visible_user(self.super_admin, self.employee_a.pk))


class UserModelTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org")

    def test_email_is_stored_in_lower_case(self):
        user = make_user("  Mixed@Case.Test ", Role.EMPLOYEE, self.org)
        self.assertEqual(user.email, "mixed@case.test")

    def test_duplicate_email_is_rejected(self):
        make_user("same@x.test", Role.EMPLOYEE, self.org)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_user("SAME@x.test", Role.EMPLOYEE, self.org)

    def test_employee_without_organization_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_user("lost@x.test", Role.EMPLOYEE, None)

    def test_super_admin_with_organization_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_user("super@x.test", Role.SUPER_ADMIN, self.org)

    def test_password_is_hashed(self):
        user = make_user("hash@x.test", Role.EMPLOYEE, self.org)
        self.assertNotIn("a-long-test-password-1", user.password)
        self.assertTrue(user.check_password("a-long-test-password-1"))
