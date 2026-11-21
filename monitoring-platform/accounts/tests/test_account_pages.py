from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role, User
from accounts.tests.factories import make_membership
from audit.models import AuditLog
from organizations.models import Organization

PASSWORD = "a-long-test-password-1"
NEW_PASSWORD = "another-long-password-2"


def password_form(old=PASSWORD, new=NEW_PASSWORD):
    return {"old_password": old, "new_password1": new, "new_password2": new}


class AccountPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Org")
        cls.other_org = Organization.objects.create(name="Other")
        cls.employee = User.objects.create_user(
            email="emp@org.test", password=PASSWORD, full_name="Erin", role=Role.EMPLOYEE, organization=cls.org
        )
        make_membership(cls.employee)
        cls.other = User.objects.create_user(
            email="other@other.test", password=PASSWORD, full_name="Olga", role=Role.EMPLOYEE, organization=cls.other_org
        )
        make_membership(cls.other)
        cls.super_admin = User.objects.create_superuser(email="root@platform.test", password=PASSWORD)

    def test_both_pages_need_a_signed_in_user(self):
        for name in ("profile", "password_change"):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response["Location"])

    def test_every_role_can_open_their_profile(self):
        for user in (self.employee, self.super_admin):
            self.client.force_login(user)
            with self.subTest(role=user.role):
                self.assertEqual(self.client.get(reverse("profile")).status_code, 200)
                self.assertEqual(self.client.get(reverse("password_change")).status_code, 200)

    def test_the_name_can_be_changed(self):
        self.client.force_login(self.employee)
        response = self.client.post(reverse("profile"), {"full_name": "Erin Updated"})
        self.assertRedirects(response, reverse("profile"))
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.full_name, "Erin Updated")

    def test_only_the_name_is_saved_from_the_profile_form(self):
        self.client.force_login(self.employee)
        self.client.post(
            reverse("profile"),
            {"full_name": "Erin", "email": "x@evil.test", "role": Role.ADMIN, "organization": self.other_org.pk},
        )
        self.employee.refresh_from_db()
        self.assertEqual(
            (self.employee.email, self.employee.role, self.employee.organization_id),
            ("emp@org.test", Role.EMPLOYEE, self.org.pk),
        )

    def test_a_name_change_is_audited_with_the_old_and_new_names(self):
        self.client.force_login(self.employee)
        self.client.post(reverse("profile"), {"full_name": "Erin Updated"})
        entry = AuditLog.objects.get(action="user.name_changed")
        self.assertEqual(entry.actor_id, self.employee.pk)
        self.assertEqual(entry.organization_id, self.org.pk)
        self.assertEqual(entry.target_type, "accounts.User")
        self.assertEqual(entry.target_id, str(self.employee.pk))
        self.assertEqual(entry.detail, "from 'Erin' to 'Erin Updated'")

    def test_an_unchanged_name_writes_no_audit_entry(self):
        self.client.force_login(self.employee)
        self.client.post(reverse("profile"), {"full_name": "Erin"})
        self.assertFalse(AuditLog.objects.filter(action="user.name_changed").exists())

    def test_a_name_that_differs_only_in_spaces_is_not_a_change(self):
        self.client.force_login(self.employee)
        self.client.post(reverse("profile"), {"full_name": "  Erin  "})
        self.assertFalse(AuditLog.objects.filter(action="user.name_changed").exists())

    def test_a_refused_name_writes_no_audit_entry(self):
        self.client.force_login(self.employee)
        self.client.post(reverse("profile"), {"full_name": ""})
        self.assertFalse(AuditLog.objects.filter(action="user.name_changed").exists())

    def test_the_name_audit_holds_no_email_and_long_names_still_fit(self):
        self.client.force_login(self.employee)
        self.client.post(reverse("profile"), {"full_name": "N" * 200})
        entry = AuditLog.objects.get(action="user.name_changed")
        self.assertNotIn("emp@org.test", entry.detail)
        self.assertLessEqual(len(entry.detail), 300)
        self.assertIn("'Erin'", entry.detail)

    def test_a_blank_name_is_refused(self):
        self.client.force_login(self.employee)
        response = self.client.post(reverse("profile"), {"full_name": "   "})
        self.assertEqual(response.status_code, 200)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.full_name, "Erin")

    def test_a_profile_change_never_touches_another_user(self):
        self.client.force_login(self.employee)
        self.client.post(reverse("profile"), {"full_name": "Changed"})
        self.other.refresh_from_db()
        self.assertEqual(self.other.full_name, "Olga")

    def test_wrong_current_password_changes_nothing(self):
        self.client.force_login(self.employee)
        response = self.client.post(reverse("password_change"), password_form(old="wrong"))
        self.assertEqual(response.status_code, 200)
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.check_password(PASSWORD))
        self.assertFalse(AuditLog.objects.filter(action="user.password_changed").exists())

    def test_weak_new_password_is_refused_by_the_validators(self):
        self.client.force_login(self.employee)
        response = self.client.post(reverse("password_change"), password_form(new="12345678"))
        self.assertEqual(response.status_code, 200)
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.check_password(PASSWORD))

    def test_changing_the_password_keeps_this_session_and_signs_out_the_others(self):
        other_session = Client()
        other_session.force_login(self.employee)
        self.client.force_login(self.employee)
        response = self.client.post(reverse("password_change"), password_form())
        self.assertRedirects(response, reverse("password_change"))
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.check_password(NEW_PASSWORD))
        self.assertEqual(self.client.get(reverse("profile")).status_code, 200)
        self.assertEqual(other_session.get(reverse("profile")).status_code, 302)

    def test_the_password_change_is_audited_without_the_password(self):
        self.client.force_login(self.employee)
        self.client.post(reverse("password_change"), password_form())
        entry = AuditLog.objects.get(action="user.password_changed")
        self.assertEqual(entry.actor_id, self.employee.pk)
        self.assertEqual(entry.organization_id, self.org.pk)
        for secret in (PASSWORD, NEW_PASSWORD):
            self.assertNotIn(secret, entry.detail)

    def test_the_sign_in_page_has_labelled_fields_and_the_reset_link(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, 'for="id_username"')
        self.assertContains(response, 'for="id_password"')
        self.assertContains(response, "Forgot your password?")
