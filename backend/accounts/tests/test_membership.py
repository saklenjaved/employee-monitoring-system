"""One open membership per person, and sign-in only through an active membership."""
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import Membership, MembershipStatus, Role, User
from accounts.tests.factories import PASSWORD, make_membership, make_org, make_super_admin


class MembershipRuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Acme")
        cls.other_org = make_org("Other")

    def make_employee(self, email="emp@acme.test"):
        return User.objects.create_user(
            email=email, password=PASSWORD, full_name="Erin", role=Role.EMPLOYEE, organization=self.org
        )

    def test_a_person_cannot_have_two_open_memberships(self):
        user = self.make_employee()
        make_membership(user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(
                user=user, organization=self.other_org, role=Role.EMPLOYEE, status=MembershipStatus.PENDING
            )

    def test_an_active_membership_needs_a_role(self):
        user = self.make_employee()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(user=user, organization=self.org, role="", status=MembershipStatus.ACTIVE)

    def test_an_ended_membership_keeps_its_history_and_frees_the_person(self):
        user = self.make_employee()
        old = make_membership(user)
        old.status = MembershipStatus.ENDED
        old.end_reason = "removed"
        old.save()
        new = Membership.objects.create(
            user=user, organization=self.other_org, role="", status=MembershipStatus.PENDING
        )
        self.assertEqual(user.memberships.count(), 2)
        self.assertEqual(new.status, MembershipStatus.PENDING)


class SignInThroughMembershipTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Acme")

    def make_employee(self):
        return User.objects.create_user(
            email="emp@acme.test", password=PASSWORD, full_name="Erin", role=Role.EMPLOYEE, organization=self.org
        )

    def test_login_is_refused_without_an_active_membership(self):
        self.make_employee()
        response = self.client.post(reverse("login"), {"username": "emp@acme.test", "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(response, "Please enter a correct")

    def test_login_works_with_an_active_membership(self):
        user = self.make_employee()
        make_membership(user)
        response = self.client.post(reverse("login"), {"username": "emp@acme.test", "password": PASSWORD})
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)

    def test_a_signed_in_person_whose_membership_ended_is_signed_out(self):
        user = self.make_employee()
        membership = make_membership(user)
        self.client.force_login(user)
        membership.status = MembershipStatus.ENDED
        membership.end_reason = "removed"
        membership.save()
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_the_super_admin_needs_no_membership(self):
        super_admin = make_super_admin()
        self.client.force_login(super_admin)
        response = self.client.get(reverse("organization_list"))
        self.assertEqual(response.status_code, 200)
