"""The same email in several organizations: a separate account (own password and status) in each one.

Organization A: admin@example.com allowed. Organization B: admin@example.com allowed.
Organization A: admin@example.com a second time: refused.
"""
import re

from django.core import mail
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.activation import MAX_WRONG_TRIES, latest_code
from accounts.models import Membership, MembershipStatus, Role, User
from accounts.services import activate_membership, open_invitation
from accounts.tests.factories import PASSWORD, make_org, make_super_admin, make_user
from accounts.transfers import SEVERAL_ACCOUNTS_MESSAGE, start_transfer

EMAIL = "admin@example.com"
PASSWORD_A = "a-long-password-for-org-a-1"
PASSWORD_B = "a-long-password-for-org-b-2"


def make_account(email, role, organization, password):
    user = make_user(email, role, organization)
    user.set_password(password)
    user.save()
    return user


class DatabaseRuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")

    def test_the_same_email_is_allowed_in_different_organizations(self):
        a = make_user(EMAIL, Role.ADMIN, self.org_a)
        b = make_user(EMAIL, Role.ADMIN, self.org_b)
        self.assertNotEqual(a.pk, b.pk)
        self.assertEqual(User.objects.filter(email=EMAIL).count(), 2)

    def test_the_same_email_twice_in_one_organization_is_refused_by_the_database(self):
        make_user(EMAIL, Role.ADMIN, self.org_a)
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(email=EMAIL, password=PASSWORD, full_name="Copy", role=Role.EMPLOYEE, organization=self.org_a)

    def test_upper_case_is_the_same_email(self):
        make_user(EMAIL, Role.ADMIN, self.org_a)
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(email="ADMIN@Example.com", password=PASSWORD, full_name="Copy", role=Role.ADMIN, organization=self.org_a)

    def test_a_super_admin_email_stays_unique_across_the_platform(self):
        make_super_admin(EMAIL)
        with self.assertRaises(ValueError):
            make_super_admin(EMAIL)
        make_user("busy@example.com", Role.ADMIN, self.org_a)
        with self.assertRaises(ValueError):
            make_super_admin("busy@example.com")
        # Even a direct insert of a second Super Admin with the same email is refused by the database.
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(email=EMAIL, password=PASSWORD, full_name="Copy", role=Role.SUPER_ADMIN)


class InvitationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.super_admin = make_super_admin()
        cls.admin_a = make_user("boss@a.test", Role.ADMIN, cls.org_a)
        cls.admin_b = make_user("boss@b.test", Role.ADMIN, cls.org_b)

    def setUp(self):
        cache.clear()

    def test_invitations_in_two_organizations_make_two_pending_accounts(self):
        user_a, membership_a, _ = open_invitation(self.admin_a, self.org_a, EMAIL, "Person", Role.EMPLOYEE)
        user_b, membership_b, _ = open_invitation(self.admin_b, self.org_b, EMAIL, "Person", Role.EMPLOYEE)
        self.assertNotEqual(user_a.pk, user_b.pk)
        self.assertEqual((user_a.organization, user_b.organization), (self.org_a, self.org_b))
        self.assertEqual({membership_a.status, membership_b.status}, {MembershipStatus.PENDING})

    def test_a_second_invitation_in_the_same_organization_is_refused(self):
        open_invitation(self.admin_a, self.org_a, EMAIL, "Person", Role.EMPLOYEE)
        with self.assertRaisesMessage(ValueError, "already has an open invitation or membership in your organization"):
            open_invitation(self.admin_a, self.org_a, EMAIL, "Person", Role.EMPLOYEE)
        self.assertEqual(User.objects.filter(email=EMAIL).count(), 1)

    def test_the_super_admin_email_can_never_be_invited(self):
        for organization, actor in ((self.org_a, self.admin_a), (self.org_b, self.admin_b)):
            with self.assertRaises(ValueError):
                open_invitation(actor, organization, self.super_admin.email, "Copy", Role.ADMIN)
        self.assertEqual(User.objects.filter(email=self.super_admin.email).count(), 1)

    def test_the_super_admin_page_creates_admins_with_the_same_email_in_two_organizations(self):
        self.client.force_login(self.super_admin)
        for org in (self.org_a, self.org_b):
            self.client.post(reverse("organization_admin_create", args=[org.pk]), {"email": EMAIL, "full_name": "Admin"})
        response = self.client.post(reverse("organization_admin_create", args=[self.org_a.pk]), {"email": EMAIL, "full_name": "Again"})
        self.assertContains(response, "already exists in this organization")
        self.assertEqual(
            sorted(User.objects.filter(email=EMAIL).values_list("organization__name", flat=True)), ["Org A", "Org B"]
        )


class ActivationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("boss@a.test", Role.ADMIN, cls.org_a)
        cls.admin_b = make_user("boss@b.test", Role.ADMIN, cls.org_b)

    def setUp(self):
        cache.clear()
        self.request = RequestFactory().post("/activate/")
        _, self.membership_a, self.code_a = open_invitation(self.admin_a, self.org_a, EMAIL, "Person", Role.EMPLOYEE)
        _, self.membership_b, self.code_b = open_invitation(self.admin_b, self.org_b, EMAIL, "Person", Role.EMPLOYEE)

    def test_each_code_activates_only_its_own_organizations_account(self):
        user, error = activate_membership(self.request, EMAIL, self.code_b, PASSWORD_B)
        self.assertEqual(error, "")
        self.assertEqual(user.organization, self.org_b)
        self.membership_a.refresh_from_db()
        self.assertEqual(self.membership_a.status, MembershipStatus.PENDING)  # organization A is untouched
        self.assertEqual(latest_code(self.membership_a).attempts, 0)  # and lost no try

        user, error = activate_membership(self.request, EMAIL, self.code_a, PASSWORD_A)
        self.assertEqual(error, "")
        self.assertEqual(user.organization, self.org_a)
        accounts = {account.organization_id: account for account in User.objects.filter(email=EMAIL)}
        self.assertTrue(accounts[self.org_a.pk].check_password(PASSWORD_A))
        self.assertTrue(accounts[self.org_b.pk].check_password(PASSWORD_B))
        self.assertFalse(accounts[self.org_a.pk].check_password(PASSWORD_B))

    def test_a_wrong_code_counts_against_every_pending_invitation_of_the_email(self):
        wrong = next(code for code in ("00000000", "11111111", "22222222") if code not in (self.code_a, self.code_b))
        _, error = activate_membership(self.request, EMAIL, wrong, PASSWORD_A)
        self.assertIn("not correct", error)
        self.assertEqual(latest_code(self.membership_a).attempts, 1)
        self.assertEqual(latest_code(self.membership_b).attempts, 1)

    def test_several_invitations_never_give_extra_guesses(self):
        wrong = next(code for code in ("00000000", "11111111", "22222222") if code not in (self.code_a, self.code_b))
        for _ in range(MAX_WRONG_TRIES):
            activate_membership(self.request, EMAIL, wrong, PASSWORD_A)
        _, error = activate_membership(self.request, EMAIL, self.code_a, PASSWORD_A)
        self.assertIn("locked", error)
        self.assertFalse(User.objects.filter(email=EMAIL, is_active=True).exists())


class LoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.account_a = make_account(EMAIL, Role.ADMIN, cls.org_a, PASSWORD_A)
        cls.account_b = make_account(EMAIL, Role.EMPLOYEE, cls.org_b, PASSWORD_B)
        cls.url = reverse("login")

    def setUp(self):
        cache.clear()

    def login(self, password, organization=""):
        data = {"username": EMAIL, "password": password}
        if organization:
            data["organization"] = organization
        return self.client.post(self.url, data)

    def signed_in_user_id(self):
        return self.client.session.get("_auth_user_id")

    def test_each_organizations_password_signs_in_to_that_organizations_account(self):
        self.login(PASSWORD_A)
        self.assertEqual(self.signed_in_user_id(), str(self.account_a.pk))
        self.client.logout()
        self.login(PASSWORD_B)
        self.assertEqual(self.signed_in_user_id(), str(self.account_b.pk))

    def test_the_signed_in_account_only_sees_its_own_organization(self):
        self.login(PASSWORD_B)
        response = self.client.get(reverse("dashboard_me"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Org B")
        self.assertNotContains(response, "Org A")
        self.assertEqual(self.client.get(reverse("dashboard_organization")).status_code, 403)  # B's account is an Employee

    def test_a_shared_password_asks_which_organization_and_never_guesses(self):
        self.account_b.set_password(PASSWORD_A)
        self.account_b.save()
        response = self.login(PASSWORD_A)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.signed_in_user_id())
        self.assertContains(response, "accounts in more than one organization")
        self.assertContains(response, "Org A")
        self.assertContains(response, "Org B")
        self.assertContains(response, f'value="{self.org_b.pk}"')

        self.login(PASSWORD_A, organization=str(self.org_b.pk))
        self.assertEqual(self.signed_in_user_id(), str(self.account_b.pk))

    def test_being_asked_to_choose_is_not_counted_as_a_failed_login(self):
        self.account_b.set_password(PASSWORD_A)
        self.account_b.save()
        for _ in range(12):  # more than the failure limit
            response = self.login(PASSWORD_A)
        self.assertEqual(response.status_code, 200)
        self.login(PASSWORD_A, organization=str(self.org_a.pk))
        self.assertEqual(self.signed_in_user_id(), str(self.account_a.pk))

    def test_an_organization_that_does_not_match_the_password_is_not_accepted(self):
        self.account_b.set_password(PASSWORD_A)
        self.account_b.save()
        other = make_org("Org C")
        response = self.login(PASSWORD_A, organization=str(other.pk))
        self.assertIsNone(self.signed_in_user_id())
        self.assertContains(response, "Choose the organization")

    def test_a_wrong_password_shows_no_organization_names(self):
        response = self.login("not-the-password-at-all")
        self.assertIsNone(self.signed_in_user_id())
        self.assertNotContains(response, "Org A")
        self.assertNotContains(response, "Org B")
        self.assertNotContains(response, "more than one organization")

    def test_a_deactivated_organization_is_skipped_when_the_password_fits_both(self):
        self.account_b.set_password(PASSWORD_A)
        self.account_b.save()
        self.org_a.is_active = False
        self.org_a.save()
        self.login(PASSWORD_A)
        self.assertEqual(self.signed_in_user_id(), str(self.account_b.pk))

    def test_a_deactivated_account_in_one_organization_does_not_block_the_other(self):
        self.account_a.is_active = False
        self.account_a.save()
        self.login(PASSWORD_B)
        self.assertEqual(self.signed_in_user_id(), str(self.account_b.pk))
        self.client.logout()
        response = self.login(PASSWORD_A)
        self.assertIsNone(self.signed_in_user_id())
        self.assertContains(response, "deactivated")


class PasswordResetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.account_a = make_account(EMAIL, Role.ADMIN, cls.org_a, PASSWORD_A)
        cls.account_b = make_account(EMAIL, Role.EMPLOYEE, cls.org_b, PASSWORD_B)

    def setUp(self):
        cache.clear()

    def test_each_account_gets_its_own_link_naming_its_organization(self):
        self.client.post(reverse("password_reset"), {"email": EMAIL})
        self.assertEqual(len(mail.outbox), 2)
        subjects = sorted(message.subject for message in mail.outbox)
        self.assertEqual(subjects, ["Reset your Employee Monitoring password (Org A)", "Reset your Employee Monitoring password (Org B)"])

        message_b = next(message for message in mail.outbox if "Org B" in message.subject)
        link = re.search(r"https?://\S+/password-reset/\S+/", message_b.body).group(0)
        response = self.client.get(link, follow=True)
        new_password = "a-brand-new-password-for-b-3"
        self.client.post(response.redirect_chain[-1][0], {"new_password1": new_password, "new_password2": new_password})

        self.account_a.refresh_from_db()
        self.account_b.refresh_from_db()
        self.assertTrue(self.account_b.check_password(new_password))
        self.assertTrue(self.account_a.check_password(PASSWORD_A))  # organization A's password did not change


class IsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("boss@a.test", Role.ADMIN, cls.org_a)
        cls.person_a = make_user(EMAIL, Role.EMPLOYEE, cls.org_a, "Person in A")
        cls.person_b = make_user(EMAIL, Role.EMPLOYEE, cls.org_b, "Person in B")

    def test_an_admin_sees_only_their_own_organizations_account_for_the_email(self):
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("person_list"))
        self.assertContains(response, "Person in A")
        self.assertNotContains(response, "Person in B")
        self.assertEqual(self.client.get(reverse("person_detail", args=[self.person_b.pk])).status_code, 404)

    def test_a_transfer_refuses_an_email_with_accounts_in_several_organizations(self):
        super_admin = make_super_admin()
        with self.assertRaisesMessage(ValueError, SEVERAL_ACCOUNTS_MESSAGE):
            start_transfer(super_admin, EMAIL, make_org("Org C"))

    def test_a_transfer_into_an_organization_that_has_the_email_is_refused(self):
        super_admin = make_super_admin()
        only_a = make_user("solo@example.com", Role.EMPLOYEE, self.org_a)
        make_user("solo@example.com", Role.EMPLOYEE, self.org_b)
        Membership.objects.filter(user__email="solo@example.com", organization=self.org_b).update(
            status=MembershipStatus.ENDED, end_reason="removed"
        )
        with self.assertRaisesMessage(ValueError, "already has an account in the destination organization"):
            start_transfer(super_admin, "solo@example.com", self.org_b)
        self.assertEqual(only_a.memberships.get().status, MembershipStatus.ACTIVE)
