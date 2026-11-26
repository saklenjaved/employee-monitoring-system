"""Activation with the 8-digit code: the rules, the page, and tenant isolation for pending people."""
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.activation import MAX_WRONG_TRIES, issue_code, latest_code
from accounts.models import ActivationCode, Membership, MembershipStatus, Role
from accounts.services import (
    ACTIVATE_LIMIT_PER_EMAIL,
    EXPIRED_MESSAGE,
    INVALID_MESSAGE,
    LIMITED_MESSAGE,
    LOCKED_MESSAGE,
    PASSWORD_NEEDED_MESSAGE,
    USED_MESSAGE,
    activate_membership,
    open_invitation,
)
from accounts.tests.factories import PASSWORD, make_org, make_super_admin, make_user
from audit.models import AuditLog
from devices.models import Device

NEW_PASSWORD = "a-long-new-password-2"


class ActivationServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.super_admin = make_super_admin()

    def setUp(self):
        cache.clear()  # attempts are counted in the cache
        self.request = RequestFactory().get("/")

    def invite(self, email="new@a.test", organization=None, role=Role.EMPLOYEE):
        """Invite and return (user, membership, plain code). The code is only known here, as in the email."""
        return open_invitation(self.admin_a, organization or self.org_a, email, "New Person", role)

    def test_a_new_person_activates_with_the_code_and_sets_their_password(self):
        user, membership, code = self.invite()
        activated, error = activate_membership(self.request, "New@A.test", code, NEW_PASSWORD)
        self.assertEqual(error, "")
        user.refresh_from_db()
        membership.refresh_from_db()
        self.assertEqual(membership.status, MembershipStatus.ACTIVE)
        self.assertIsNotNone(membership.activated_at)
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password(NEW_PASSWORD))
        self.assertEqual((user.organization, user.role), (self.org_a, Role.EMPLOYEE))
        self.assertTrue(AuditLog.objects.filter(action="user.activated", target_id=str(user.pk)).exists())

    def test_a_new_person_must_choose_a_password_and_keeps_the_code(self):
        user, membership, code = self.invite()
        _, error = activate_membership(self.request, "new@a.test", code, "")
        self.assertEqual(error, PASSWORD_NEEDED_MESSAGE)
        membership.refresh_from_db()
        self.assertEqual(membership.status, MembershipStatus.PENDING)
        _, error = activate_membership(self.request, "new@a.test", code, NEW_PASSWORD)
        self.assertEqual(error, "")

    def test_a_weak_password_is_refused_and_the_code_still_works(self):
        user, membership, code = self.invite()
        _, error = activate_membership(self.request, "new@a.test", code, "password")
        self.assertTrue(error)
        membership.refresh_from_db()
        self.assertEqual(membership.status, MembershipStatus.PENDING)
        _, error = activate_membership(self.request, "new@a.test", code, NEW_PASSWORD)
        self.assertEqual(error, "")

    def test_a_person_invited_back_to_the_same_organization_keeps_their_password(self):
        person = make_user("back@a.test", Role.EMPLOYEE, self.org_a)
        old = person.memberships.get()
        old.status, old.end_reason = MembershipStatus.ENDED, "removed"
        old.save()
        user, membership, code = self.invite("back@a.test", organization=self.org_a)
        activated, error = activate_membership(self.request, "back@a.test", code, "")
        self.assertEqual(error, "")
        activated.refresh_from_db()
        self.assertEqual(activated.pk, person.pk)  # the same account of this organization
        self.assertTrue(activated.check_password(PASSWORD))
        self.assertEqual(Membership.objects.filter(user=activated, status=MembershipStatus.ACTIVE).count(), 1)
        self.assertEqual(activated.memberships.count(), 2)  # the old one stays as history

    def test_the_same_email_in_another_organization_gets_its_own_account_and_password(self):
        person = make_user("both@a.test", Role.EMPLOYEE, self.org_a)
        user, membership, code = self.invite("both@a.test", organization=self.org_b)
        self.assertNotEqual(user.pk, person.pk)
        _, error = activate_membership(self.request, "both@a.test", code, "")
        self.assertEqual(error, "Choose a password to finish activation.")  # a new account needs its own password
        activated, error = activate_membership(self.request, "both@a.test", code, NEW_PASSWORD)
        self.assertEqual(error, "")
        self.assertEqual((activated.pk, activated.organization), (user.pk, self.org_b))
        self.assertTrue(activated.check_password(NEW_PASSWORD))
        person.refresh_from_db()
        self.assertTrue(person.check_password(PASSWORD))  # the first organization's account is unchanged
        self.assertEqual(person.organization, self.org_a)
        self.assertEqual(person.memberships.get().status, MembershipStatus.ACTIVE)

    def test_activation_never_registers_a_device(self):
        _, _, code = self.invite()
        activate_membership(self.request, "new@a.test", code, NEW_PASSWORD)
        self.assertFalse(Device.objects.exists())

    def test_a_wrong_code_says_how_many_tries_are_left(self):
        _, _, code = self.invite()
        wrong = "00000000" if code != "00000000" else "11111111"
        _, error = activate_membership(self.request, "new@a.test", wrong, NEW_PASSWORD)
        self.assertEqual(error, f"The code is not correct. Tries left before it locks: {MAX_WRONG_TRIES - 1}.")

    def test_five_wrong_tries_lock_the_code_even_for_the_right_one(self):
        user, membership, code = self.invite()
        wrong = "00000000" if code != "00000000" else "11111111"
        errors = [activate_membership(self.request, "new@a.test", wrong, NEW_PASSWORD)[1] for _ in range(MAX_WRONG_TRIES)]
        self.assertIn(LOCKED_MESSAGE, errors)
        _, error = activate_membership(self.request, "new@a.test", code, NEW_PASSWORD)
        self.assertEqual(error, LOCKED_MESSAGE)
        membership.refresh_from_db()
        self.assertEqual(membership.status, MembershipStatus.PENDING)

    def test_an_expired_code_is_refused(self):
        _, _, code = self.invite()
        ActivationCode.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        _, error = activate_membership(self.request, "new@a.test", code, NEW_PASSWORD)
        self.assertEqual(error, EXPIRED_MESSAGE)

    def test_a_used_code_cannot_be_used_again(self):
        _, _, code = self.invite()
        activate_membership(self.request, "new@a.test", code, NEW_PASSWORD)
        _, error = activate_membership(self.request, "new@a.test", code, NEW_PASSWORD)
        self.assertEqual(error, USED_MESSAGE)

    def test_a_new_code_makes_the_old_one_stop_working(self):
        user, membership, old_code = self.invite()
        new_code = issue_code(membership)
        self.assertNotEqual(old_code, new_code)
        _, error = activate_membership(self.request, "new@a.test", old_code, NEW_PASSWORD)
        self.assertTrue(error)
        _, error = activate_membership(self.request, "new@a.test", new_code, NEW_PASSWORD)
        self.assertEqual(error, "")

    def test_an_unknown_email_gets_the_same_generic_message(self):
        _, _, code = self.invite()
        _, error = activate_membership(self.request, "nobody@a.test", code, NEW_PASSWORD)
        self.assertEqual(error, INVALID_MESSAGE)

    def test_too_many_attempts_for_one_email_are_refused_for_a_while(self):
        _, _, code = self.invite()
        wrong = "00000000" if code != "00000000" else "11111111"
        for _ in range(ACTIVATE_LIMIT_PER_EMAIL):
            activate_membership(self.request, "new@a.test", wrong, NEW_PASSWORD)
        _, error = activate_membership(self.request, "new@a.test", code, NEW_PASSWORD)
        self.assertEqual(error, LIMITED_MESSAGE)

    def test_the_latest_code_is_the_only_one_checked(self):
        user, membership, code = self.invite()
        self.assertEqual(latest_code(membership).membership, membership)
        self.assertEqual(ActivationCode.objects.filter(membership=membership, used_at__isnull=True).count(), 1)


class ActivationPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Acme")
        cls.admin = make_user("admin@acme.test", Role.ADMIN, cls.org)

    def setUp(self):
        cache.clear()
        mail.outbox = []

    def test_the_page_opens_without_signing_in(self):
        response = self.client.get(reverse("activate"))
        self.assertContains(response, "Activate your account")
        self.assertContains(response, 'name="code"')

    def test_a_valid_code_activates_and_signs_the_employee_in_at_their_dashboard(self):
        _, _, code = open_invitation(self.admin, self.org, "new@acme.test", "New", Role.EMPLOYEE)
        response = self.client.post(
            reverse("activate"),
            {"email": "new@acme.test", "code": code, "password": NEW_PASSWORD, "password_confirm": NEW_PASSWORD},
            follow=True,
        )
        self.assertRedirects(response, reverse("dashboard_me"))
        self.assertContains(response, "Your account is active.")
        self.assertIn("_auth_user_id", self.client.session)

    def test_a_wrong_code_shows_the_error_on_the_page(self):
        open_invitation(self.admin, self.org, "new@acme.test", "New", Role.EMPLOYEE)
        response = self.client.post(
            reverse("activate"), {"email": "new@acme.test", "code": "12345678", "password": NEW_PASSWORD, "password_confirm": NEW_PASSWORD}
        )
        self.assertContains(response, "Tries left before it locks")

    def test_a_code_that_is_not_eight_digits_is_refused_before_checking(self):
        response = self.client.post(reverse("activate"), {"email": "new@acme.test", "code": "1234", "password": "", "password_confirm": ""})
        self.assertContains(response, "Ensure this value has at least 8 characters")

    def test_mismatched_passwords_are_refused(self):
        response = self.client.post(
            reverse("activate"),
            {"email": "new@acme.test", "code": "12345678", "password": "one", "password_confirm": "two"},
        )
        self.assertContains(response, "The two passwords do not match.")

    def test_an_existing_person_with_a_pending_invitation_is_told_to_activate_after_the_right_password(self):
        person = make_user("back@acme.test", Role.EMPLOYEE, self.org)
        old = person.memberships.get()
        old.status, old.end_reason = MembershipStatus.ENDED, "removed"
        old.save()
        open_invitation(self.admin, self.org, "back@acme.test", "Back", Role.EMPLOYEE)
        response = self.client.post(reverse("login"), {"username": "back@acme.test", "password": PASSWORD})
        self.assertContains(response, "Activate it with the code from your invitation email.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_wrong_password_for_a_pending_person_gets_the_generic_message(self):
        open_invitation(self.admin, self.org, "new@acme.test", "New", Role.EMPLOYEE)
        response = self.client.post(reverse("login"), {"username": "new@acme.test", "password": "guess-it-now-1"})
        self.assertNotContains(response, "not active yet")
        self.assertContains(response, "Please enter a correct")


class PendingPeopleInWorkforceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.admin_b = make_user("admin@b.test", Role.ADMIN, cls.org_b)
        cls.pending, cls.pending_membership, _ = open_invitation(cls.admin_a, cls.org_a, "wait@a.test", "Waiting", Role.EMPLOYEE)

    def test_the_list_shows_pending_people_as_pending_not_deactivated(self):
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("person_list"))
        self.assertContains(response, "<td>Pending activation</td>")
        self.assertNotContains(response, "<td>Deactivated</td>")

    def test_the_deactivated_filter_does_not_include_pending_people(self):
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("person_list"), {"status": "deactivated"})
        self.assertNotContains(response, "wait@a.test")
        response = self.client.get(reverse("person_list"), {"status": "pending"})
        self.assertContains(response, "wait@a.test")

    def test_an_admin_cannot_activate_a_pending_person_by_hand(self):
        self.client.force_login(self.admin_a)
        response = self.client.post(reverse("person_set_active", args=[self.pending.pk]), {"active": "1"}, follow=True)
        self.assertContains(response, "This person has not activated the account yet.")
        self.pending.refresh_from_db()
        self.assertFalse(self.pending.is_active)

    def test_another_organizations_admin_cannot_see_the_pending_person(self):
        self.client.force_login(self.admin_b)
        self.assertEqual(self.client.get(reverse("person_detail", args=[self.pending.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("person_resend_invite", args=[self.pending.pk])).status_code, 404)
        self.assertEqual(len(mail.outbox), 0)


class DeactivatedLoginTests(TestCase):
    """A person who cannot sign in is told why, but only after the right password is typed."""

    def setUp(self):
        from accounts.services import set_user_active

        self.org = make_org("Acme")
        self.admin = make_user("admin@acme.test", Role.ADMIN, self.org)
        self.person = make_user("sam@acme.test", Role.EMPLOYEE, self.org)
        self.set_user_active = set_user_active

    def test_a_deactivated_by_admin_gets_a_clear_message(self):
        self.set_user_active(self.admin, self.person, False)
        response = self.client.post(reverse("login"), {"username": "sam@acme.test", "password": PASSWORD})
        self.assertContains(response, "deactivated by your administrator")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_removed_person_gets_an_ended_access_message(self):

        membership = self.person.memberships.get()
        membership.status, membership.end_reason = MembershipStatus.ENDED, "removed"
        membership.save()
        self.person.is_active = False
        self.person.save()
        response = self.client.post(reverse("login"), {"username": "sam@acme.test", "password": PASSWORD})
        self.assertContains(response, "access to this organization has ended")

    def test_a_wrong_password_for_a_deactivated_person_stays_generic(self):
        self.set_user_active(self.admin, self.person, False)
        response = self.client.post(reverse("login"), {"username": "sam@acme.test", "password": "guess-it-now-1"})
        self.assertNotContains(response, "deactivated by your administrator")
        self.assertContains(response, "Please enter a correct")
