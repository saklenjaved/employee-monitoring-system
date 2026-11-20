"""Invitations: the activation email, who may be invited, and who may get a new code."""
import re
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.activation import CODE_LIFETIME
from accounts.checks import invitation_email_is_configured
from accounts.models import ActivationCode, Membership, MembershipStatus, Role, User
from accounts.services import (
    REFUSED_MESSAGE,
    RESEND_INVITE_LIMIT,
    open_invitation,
    send_activation_code,
)
from accounts.tests.factories import PASSWORD, make_org, make_super_admin, make_user
from audit.models import AuditLog

CODE = re.compile(r"\b(\d{8})\b")


def invite(actor, organization, email, role, full_name="New Person"):
    """Invite a person and send the code, as the Admin's form does (the request is not used for anything else)."""
    user, membership, code = open_invitation(actor, organization, email, full_name, role)
    sent = send_activation_code(RequestFactory().get("/"), membership, code)
    return user, membership, sent


class InviteEmailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Acme")
        cls.super_admin = make_super_admin()
        cls.admin = make_user("owner@acme.test", Role.ADMIN, cls.org)

    def setUp(self):
        mail.outbox = []

    def test_an_admin_invitation_names_the_role_and_organization(self):
        invite(self.super_admin, self.org, "boss@acme.test", Role.ADMIN)
        message = mail.outbox[0]
        self.assertEqual(message.subject, "Your Employee Monitoring activation code")
        self.assertIn("with the Admin role", message.body)
        self.assertIn("invited to Acme", message.body)

    def test_a_manager_invitation_does_not_say_employee(self):
        invite(self.admin, self.org, "lead@acme.test", Role.MANAGER)
        body = mail.outbox[0].body
        self.assertIn("with the Manager role", body)
        self.assertNotIn("Employee role", body)

    def test_the_email_has_one_code_and_the_activation_page_link_only(self):
        invite(self.admin, self.org, "new@acme.test", Role.EMPLOYEE)
        body = mail.outbox[0].body
        self.assertEqual(len(CODE.findall(body)), 1)
        self.assertIn("http://testserver/activate/", body)
        self.assertNotIn("/password-reset/", body)
        self.assertIn("expires in 24 hours", body)
        self.assertEqual(mail.outbox[0].to, ["new@acme.test"])

    def test_the_email_never_contains_a_password(self):
        invite(self.admin, self.org, "new@acme.test", Role.EMPLOYEE)
        self.assertNotIn("password:", mail.outbox[0].body.lower())

    def test_the_code_is_stored_only_as_a_keyed_hash(self):
        invite(self.admin, self.org, "new@acme.test", Role.EMPLOYEE)
        code = CODE.search(mail.outbox[0].body).group(1)
        stored = ActivationCode.objects.get()
        self.assertNotEqual(stored.code_hash, code)
        self.assertEqual(len(stored.code_hash), 64)
        self.assertNotIn(code, stored.code_hash)
        lifetime = stored.expires_at - stored.created_at
        self.assertLess(abs(lifetime - CODE_LIFETIME), timedelta(seconds=5))


class InvitationRuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.super_admin = make_super_admin()
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)

    def setUp(self):
        mail.outbox = []

    def test_a_new_person_gets_an_unusable_password_and_no_login_until_activation(self):
        user, membership, _ = invite(self.admin_a, self.org_a, "new@a.test", Role.EMPLOYEE)
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_active)
        self.assertEqual(membership.status, MembershipStatus.PENDING)
        response = self.client.post(reverse("login"), {"username": "new@a.test", "password": "guess-it-now-1"})
        self.assertContains(response, "Please enter a correct")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_an_existing_person_keeps_their_password_and_gets_only_a_new_membership(self):
        person = make_user("back@a.test", Role.EMPLOYEE, self.org_a)
        old = person.memberships.get()
        old.status, old.end_reason = MembershipStatus.ENDED, "removed"
        old.save()
        user, membership, _ = invite(self.admin_a, self.org_b, "back@a.test", Role.EMPLOYEE)
        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))
        self.assertEqual(User.objects.filter(email="back@a.test").count(), 1)
        self.assertEqual(membership.organization, self.org_b)
        self.assertEqual(user.memberships.count(), 2)

    def test_a_second_open_invitation_in_the_same_organization_is_refused(self):
        invite(self.admin_a, self.org_a, "twice@a.test", Role.EMPLOYEE)
        with self.assertRaisesMessage(ValueError, "already has an open invitation or membership in your organization"):
            invite(self.admin_a, self.org_a, "twice@a.test", Role.EMPLOYEE)
        self.assertEqual(Membership.objects.filter(user__email="twice@a.test").count(), 1)

    def test_an_active_membership_elsewhere_is_refused_with_a_generic_message(self):
        person = make_user("busy@a.test", Role.EMPLOYEE, self.org_a)
        with self.assertRaisesMessage(ValueError, REFUSED_MESSAGE):
            invite(self.admin_a, self.org_b, "busy@a.test", Role.EMPLOYEE)
        self.assertEqual(person.memberships.count(), 1)
        self.assertFalse(Membership.objects.filter(organization=self.org_b).exists())

    def test_the_super_admin_cannot_be_invited(self):
        with self.assertRaisesMessage(ValueError, REFUSED_MESSAGE):
            invite(self.admin_a, self.org_a, self.super_admin.email, Role.ADMIN)
        self.assertEqual(len(mail.outbox), 0)

    def test_the_super_role_cannot_be_given_by_an_invitation(self):
        with self.assertRaises(ValueError):
            invite(self.super_admin, self.org_a, "boss@a.test", Role.SUPER_ADMIN)

    def test_the_invitation_is_audited_with_the_role(self):
        invite(self.admin_a, self.org_a, "audited@a.test", Role.MANAGER)
        entry = AuditLog.objects.get(action="membership.invited")
        self.assertEqual(entry.organization, self.org_a)
        self.assertIn("role manager", entry.detail)


class ResendCodeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.admin_b = make_user("admin@b.test", Role.ADMIN, cls.org_b)
        cls.super_admin = make_super_admin()
        cls.new_employee, _, _ = invite(cls.admin_a, cls.org_a, "new@a.test", Role.EMPLOYEE)
        cls.new_admin, _, _ = invite(cls.super_admin, cls.org_a, "newadmin@a.test", Role.ADMIN)
        cls.signed_in_employee = make_user("used@a.test", Role.EMPLOYEE, cls.org_a)
        cls.employee_b = make_user("new@b.test", Role.EMPLOYEE, cls.org_b)
        cls.manager_a = make_user("m@a.test", Role.MANAGER, cls.org_a)

    def setUp(self):
        cache.clear()  # resends are counted in the cache
        mail.outbox = []

    def resend_by(self, user, target):
        self.client.force_login(user)
        return self.client.post(reverse("person_resend_invite", args=[target.pk]), follow=True)

    def test_an_admin_sends_a_new_code_to_their_own_pending_employee(self):
        response = self.resend_by(self.admin_a, self.new_employee)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["new@a.test"])
        self.assertContains(response, "A new activation code was emailed to new@a.test.")
        self.assertNotContains(response, CODE.search(mail.outbox[0].body).group(1))  # the code is never shown on the page
        self.assertTrue(AuditLog.objects.filter(action="membership.invite_resent", target_id=str(self.new_employee.pk)).exists())

    def test_a_new_code_replaces_the_unused_one(self):
        self.resend_by(self.admin_a, self.new_employee)
        self.assertEqual(ActivationCode.objects.filter(membership__user=self.new_employee, used_at__isnull=True).count(), 1)
        self.assertEqual(ActivationCode.objects.filter(membership__user=self.new_employee).count(), 1)

    def test_a_person_with_an_active_membership_cannot_get_a_new_code(self):
        response = self.resend_by(self.admin_a, self.signed_in_employee)
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(response, "This invitation cannot be resent.")

    def test_another_organizations_employee_is_not_found(self):
        self.client.force_login(self.admin_a)
        self.assertEqual(self.client.post(reverse("person_resend_invite", args=[self.employee_b.pk])).status_code, 404)
        self.assertEqual(len(mail.outbox), 0)

    def test_an_admin_cannot_resend_to_another_admin(self):
        self.client.force_login(self.admin_a)
        response = self.client.post(reverse("person_resend_invite", args=[self.new_admin.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(mail.outbox), 0)

    def test_the_super_admin_sends_a_new_code_to_a_pending_organization_admin(self):
        self.client.force_login(self.super_admin)
        response = self.client.post(reverse("organization_admin_resend_invite", args=[self.new_admin.pk]), follow=True)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["newadmin@a.test"])
        self.assertContains(response, "A new activation code was emailed to newadmin@a.test.")

    def test_the_super_admin_cannot_resend_to_an_employee(self):
        self.client.force_login(self.super_admin)
        response = self.client.post(reverse("organization_admin_resend_invite", args=[self.new_employee.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(mail.outbox), 0)

    def test_employees_and_managers_cannot_resend(self):
        for user in (self.signed_in_employee, self.manager_a):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                response = self.client.post(reverse("person_resend_invite", args=[self.new_employee.pk]))
                self.assertEqual(response.status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    def test_new_codes_are_limited_per_person(self):
        for _ in range(RESEND_INVITE_LIMIT):
            self.resend_by(self.admin_a, self.new_employee)
        self.assertEqual(len(mail.outbox), RESEND_INVITE_LIMIT)
        response = self.resend_by(self.admin_a, self.new_employee)
        self.assertEqual(len(mail.outbox), RESEND_INVITE_LIMIT)
        self.assertContains(response, "Too many codes were sent for this invitation.")


class EmailSettingsCheckTests(SimpleTestCase):
    def check(self, **settings_values):
        with override_settings(**settings_values):
            return invitation_email_is_configured(None)

    def test_development_without_a_mail_server_only_warns(self):
        warnings = self.check(DEV_MODE=True, EMAIL_HOST="", DEFAULT_FROM_EMAIL="no-reply@example.invalid")
        self.assertEqual([warning.id for warning in warnings], ["accounts.W001"])

    def test_development_with_a_mail_server_passes(self):
        self.assertEqual(self.check(DEV_MODE=True, EMAIL_HOST="smtp.gmail.com", DEFAULT_FROM_EMAIL="me@gmail.com"), [])

    def test_production_without_a_mail_server_is_an_error(self):
        errors = self.check(DEV_MODE=False, EMAIL_HOST="", DEFAULT_FROM_EMAIL="hello@acme.test")
        self.assertEqual([error.id for error in errors], ["accounts.E001"])

    def test_production_with_the_placeholder_sender_is_an_error(self):
        errors = self.check(DEV_MODE=False, EMAIL_HOST="smtp.acme.test", DEFAULT_FROM_EMAIL="no-reply@example.invalid")
        self.assertEqual([error.id for error in errors], ["accounts.E001"])

    def test_production_with_a_mail_server_and_sender_passes(self):
        self.assertEqual(self.check(DEV_MODE=False, EMAIL_HOST="smtp.acme.test", DEFAULT_FROM_EMAIL="hello@acme.test"), [])
