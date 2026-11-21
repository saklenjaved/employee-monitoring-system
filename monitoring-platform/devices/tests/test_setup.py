"""The Admin's monitoring setup: the checklist on an employee's page, the agent settings on the code page,
and the list of employees still to connect on the Admin dashboard."""
import secrets
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from accounts.tests.factories import make_org, make_user
from analytics.services import employees_to_connect
from devices.models import Device, RegistrationCode
from devices.pairing import open_setup_window
from devices.services import next_setup_step, setup_steps


def make_device(user, last_seen=None, policy_version="", active=True):
    return Device.objects.create(
        organization=user.organization,
        user=user,
        name="Test PC",
        os_version="Windows 11",
        agent_version="0.1.0",
        credential_hash=secrets.token_hex(32),
        credential_expires_at=timezone.now() + timedelta(days=30),
        last_seen_at=last_seen,
        policy_version_served=policy_version,
        is_active=active,
    )


def done_flags(steps):
    return [done for _, done in steps]


class SetupStepsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org)

    def test_a_new_employee_has_no_step_done(self):
        steps = setup_steps(self.emp)
        self.assertEqual(done_flags(steps), [False, False, False, False])
        self.assertEqual(next_setup_step(steps), steps[0][0])

    def test_steps_turn_done_in_the_order_of_the_work(self):
        open_setup_window(self.admin, self.emp)
        self.assertEqual(done_flags(setup_steps(self.emp)), [True, False, False, False])

        device = make_device(self.emp)
        self.assertEqual(done_flags(setup_steps(self.emp)), [True, True, False, False])

        Device.objects.filter(pk=device.pk).update(policy_version_served="3")
        self.assertEqual(done_flags(setup_steps(self.emp)), [True, True, True, False])

        Device.objects.filter(pk=device.pk).update(last_seen_at=timezone.now())
        steps = setup_steps(self.emp)
        self.assertEqual(done_flags(steps), [True, True, True, True])
        self.assertIsNone(next_setup_step(steps))

    def test_an_old_heartbeat_does_not_count_as_connected(self):
        make_device(self.emp, last_seen=timezone.now() - timedelta(hours=1), policy_version="3")
        self.assertFalse(setup_steps(self.emp)[3][1])

    def test_a_revoked_device_does_not_count(self):
        make_device(self.emp, last_seen=timezone.now(), policy_version="3", active=False)
        steps = done_flags(setup_steps(self.emp))
        self.assertEqual(steps[1], False)
        self.assertEqual(steps[3], False)


class EmployeesToConnectTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.manager_a = make_user("m@a.test", Role.MANAGER, cls.org_a)
        cls.waiting = make_user("waiting@a.test", Role.EMPLOYEE, cls.org_a, "Waiting Person")
        cls.connected = make_user("connected@a.test", Role.EMPLOYEE, cls.org_a, "Connected Person")
        cls.inactive = make_user("inactive@a.test", Role.EMPLOYEE, cls.org_a, "Inactive Person")
        cls.other_org = make_user("other@b.test", Role.EMPLOYEE, cls.org_b, "Other Org Person")
        make_device(cls.connected, last_seen=timezone.now(), policy_version="1")
        User.objects.filter(pk=cls.inactive.pk).update(is_active=False)

    def test_the_admin_sees_only_active_employees_of_their_organization_without_a_connected_agent(self):
        result = employees_to_connect(self.admin_a)
        self.assertEqual(result["count"], 1)
        self.assertEqual([person.pk for person in result["people"]], [self.waiting.pk])

    def test_other_roles_get_nothing(self):
        self.assertIsNone(employees_to_connect(self.manager_a))

    def test_the_admin_dashboard_lists_the_waiting_employee(self):
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("dashboard_organization"))
        self.assertContains(response, "Attention required")
        self.assertContains(response, "Waiting Person")
        self.assertNotContains(response, "Other Org Person")

    def test_a_manager_cannot_open_the_admin_dashboard(self):
        self.client.force_login(self.manager_a)
        self.assertEqual(self.client.get(reverse("dashboard_organization")).status_code, 403)


class SetupPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.emp_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a, "Employee A")
        cls.emp_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b, "Employee B")

    def test_the_employee_page_shows_the_monitoring_checklist(self):
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("person_detail", args=[self.emp_a.pk]))
        self.assertContains(response, "Monitoring setup")
        self.assertContains(response, "Sign-in link emailed and the approval window opened")
        self.assertContains(response, "Next: sign-in link emailed and the approval window opened.")

    def test_another_organizations_employee_page_is_not_found(self):
        self.client.force_login(self.admin_a)
        self.assertEqual(self.client.get(reverse("person_detail", args=[self.emp_b.pk])).status_code, 404)

    def test_the_code_page_has_no_terminal_steps(self):
        self.client.force_login(self.admin_a)
        response = self.client.post(reverse("registration_code_create", args=[self.emp_a.pk]))
        self.assertNotContains(response, "winagent")
        self.assertNotContains(response, "[api]")
        self.assertNotContains(response, "[registration]")

    def test_the_person_page_has_no_registration_code_button(self):
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("person_detail", args=[self.emp_a.pk]))
        self.assertNotContains(response, "Create registration code")
        self.assertContains(response, 'hx-get="/workforce/people/%d/setup-status/"' % self.emp_a.pk)

    def test_the_setup_card_refreshes_for_the_admin_only_within_their_organization(self):
        self.client.force_login(self.admin_a)
        url = reverse("person_setup_status", args=[self.emp_a.pk])
        response = self.client.get(url)
        self.assertContains(response, 'id="setup-status"')
        self.assertContains(response, "Sign-in link emailed and the approval window opened")
        self.assertEqual(self.client.get(reverse("person_setup_status", args=[self.emp_b.pk])).status_code, 404)
        self.client.force_login(self.emp_a)
        self.assertEqual(self.client.get(url).status_code, 403)
