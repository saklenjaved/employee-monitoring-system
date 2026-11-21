import datetime

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_super_admin, make_user
from audit.models import AuditLog
from monitoring import services
from monitoring.models import MonitoringPolicy

VALID = {
    "enabled": "on",
    "days": ["0", "1", "2"],
    "start_time": "08:00",
    "end_time": "17:30",
    "sample_interval_seconds": "20",
    "idle_threshold_seconds": "600",
    "sync_interval_seconds": "90",
    "timezone": "Asia/Kolkata",
}


class PolicyServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)

    def test_policy_is_created_with_defaults(self):
        policy = services.get_policy(self.org_a)
        self.assertTrue(policy.enabled)
        self.assertEqual(policy.days, [0, 1, 2, 3, 4])
        self.assertEqual(policy.start_time, datetime.time(9, 0))
        self.assertEqual(policy.sample_interval_seconds, 30)
        self.assertEqual(policy.idle_threshold_seconds, 300)
        self.assertEqual(MonitoringPolicy.objects.filter(organization=self.org_a).count(), 1)

    def test_each_organization_has_its_own_policy(self):
        self.assertNotEqual(services.get_policy(self.org_a).pk, services.get_policy(self.org_b).pk)

    def test_agent_policy_matches_the_contract(self):
        data = services.policy_for_agent(self.org_a)
        self.assertEqual(data["policy_version"], "1")
        self.assertEqual(data["monitoring"], {"enabled": True, "days": [0, 1, 2, 3, 4], "start": "09:00", "end": "18:00"})
        self.assertEqual(
            data["activity"],
            {"sample_interval_seconds": 30, "idle_threshold_seconds": 300, "sync_interval_seconds": 60},
        )
        self.assertEqual(data["screenshots"], {"enabled": False})
        self.assertEqual(data["exclusions"], {"apps": [], "blur_regions": []})

    def test_update_changes_version_and_is_audited(self):
        values = {
            "enabled": False,
            "days": [3, 1],
            "start_time": datetime.time(7, 0),
            "end_time": datetime.time(15, 0),
            "sample_interval_seconds": 60,
            "idle_threshold_seconds": 900,
        }
        policy = services.update_policy(self.admin_a, self.org_a, values)
        self.assertEqual(policy.version, 2)
        self.assertEqual(policy.days, [1, 3])
        self.assertFalse(policy.enabled)
        entry = AuditLog.objects.get(action="monitoring_policy.changed")
        self.assertEqual(entry.organization, self.org_a)
        self.assertEqual(entry.actor, self.admin_a)
        # The note says what changed, so switching monitoring off can be traced.
        self.assertIn("version 2", entry.detail)
        self.assertIn("enabled: True->False", entry.detail)
        self.assertIn("sample_interval_seconds: 30->60", entry.detail)

    def test_saving_the_same_values_does_not_change_the_version(self):
        policy = services.get_policy(self.org_a)
        values = {name: getattr(policy, name) for name in services.POLICY_FIELDS}
        services.update_policy(self.admin_a, self.org_a, values)
        policy.refresh_from_db()
        self.assertEqual(policy.version, 1)
        self.assertFalse(AuditLog.objects.filter(action="monitoring_policy.changed").exists())


class PolicyPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.manager_a = make_user("manager@a.test", Role.MANAGER, cls.org_a)
        cls.employee_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a)
        cls.url = reverse("policy_settings")

    def test_admin_can_open_and_save(self):
        self.client.force_login(self.admin_a)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        response = self.client.post(self.url, VALID)
        self.assertRedirects(response, self.url)
        policy = MonitoringPolicy.objects.get(organization=self.org_a)
        self.assertEqual(policy.days, [0, 1, 2])
        self.assertEqual(policy.end_time, datetime.time(17, 30))
        self.assertEqual(policy.timezone, "Asia/Kolkata")
        self.assertEqual(policy.version, 2)

    def test_saving_changes_only_the_admins_own_organization(self):
        services.get_policy(self.org_b)
        self.client.force_login(self.admin_a)
        self.client.post(self.url, VALID)
        self.assertEqual(MonitoringPolicy.objects.get(organization=self.org_b).version, 1)

    def test_invalid_values_are_refused(self):
        self.client.force_login(self.admin_a)
        bad_values = [
            {"days": []},
            {"start_time": "09:00", "end_time": "09:00"},
            {"sample_interval_seconds": "4"},
            {"sample_interval_seconds": "301"},
            {"idle_threshold_seconds": "59"},
            {"idle_threshold_seconds": "3601"},
            {"timezone": "Mars/Olympus"},
            {"timezone": ""},
        ]
        for change in bad_values:
            with self.subTest(change=change):
                response = self.client.post(self.url, {**VALID, **change})
                self.assertEqual(response.status_code, 200)
        self.assertEqual(services.get_policy(self.org_a).version, 1)

    def test_anonymous_user_is_sent_to_login(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('login')}?next={self.url}")
        self.assertEqual(self.client.post(self.url, VALID).status_code, 302)
        self.assertFalse(MonitoringPolicy.objects.exists())

    def test_other_roles_get_403(self):
        for user in (self.manager_a, self.employee_a, make_super_admin()):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.url).status_code, 403)
                self.assertEqual(self.client.post(self.url, VALID).status_code, 403)
