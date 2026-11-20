"""The monitoring PIN: stored as a hash, locked after wrong guesses, never written to the audit log."""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from accounts.pin import PIN_LOCKED, PIN_MAX_FAILURES, PIN_MISSING, PIN_OK, PIN_WRONG, check_pin, has_pin, set_pin
from accounts.tests.factories import make_org, make_user
from audit.models import AuditLog
from devices.models import MonitoringStart
from devices.tests.test_setup import make_device

PIN = "482915"
PASSWORD = "a-long-test-password"


class PinStorageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org)

    def test_pin_is_stored_only_as_a_hash(self):
        set_pin(self.emp, PIN)
        record = self.emp.monitoring_pin
        self.assertNotEqual(record.pin_hash, PIN)
        self.assertNotIn(PIN, record.pin_hash)
        self.assertEqual(check_pin(self.emp, PIN), PIN_OK)

    def test_no_pin_set_is_reported_as_missing(self):
        self.assertFalse(has_pin(self.emp))
        self.assertEqual(check_pin(self.emp, PIN), PIN_MISSING)

    def test_repeated_wrong_guesses_lock_the_pin_even_for_the_right_one(self):
        set_pin(self.emp, PIN)
        results = [check_pin(self.emp, "000000") for _ in range(PIN_MAX_FAILURES)]
        self.assertEqual(results[:-1], [PIN_WRONG] * (PIN_MAX_FAILURES - 1))
        self.assertEqual(results[-1], PIN_LOCKED)
        self.assertEqual(check_pin(self.emp, PIN), PIN_LOCKED)

    def test_the_lock_ends_after_its_time(self):
        set_pin(self.emp, PIN)
        for _ in range(PIN_MAX_FAILURES):
            check_pin(self.emp, "000000")
        later = timezone.now() + timedelta(minutes=16)
        self.assertEqual(check_pin(self.emp, PIN, now=later), PIN_OK)

    def test_a_right_guess_resets_the_count(self):
        set_pin(self.emp, PIN)
        for _ in range(PIN_MAX_FAILURES - 1):
            check_pin(self.emp, "000000")
        self.assertEqual(check_pin(self.emp, PIN), PIN_OK)
        self.assertEqual(check_pin(self.emp, "000000"), PIN_WRONG)

    def test_the_audit_log_never_holds_the_pin(self):
        set_pin(self.emp, PIN)
        for _ in range(PIN_MAX_FAILURES):
            check_pin(self.emp, PIN[::-1])
        self.assertTrue(AuditLog.objects.filter(action="monitoring_pin.locked").exists())
        for entry in AuditLog.objects.all():
            text = " ".join(str(value) for value in (entry.action, entry.detail, entry.actor_email))
            self.assertNotIn(PIN, text)
            self.assertNotIn(PIN[::-1], text)


class PinWebTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org)
        cls.emp.set_password(PASSWORD)
        cls.emp.save()
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)

    def setUp(self):
        cache.clear()
        self.client.force_login(self.emp)

    def save_pin(self, pin=PIN, confirm=PIN, password=PASSWORD):
        return self.client.post(
            reverse("monitoring_pin_set"),
            {"current_password": password, "pin": pin, "pin_confirm": confirm},
            follow=True,
        )

    def start(self):
        return self.client.post(reverse("monitoring_start"), follow=True)

    def test_employee_sets_a_pin_with_the_current_password(self):
        self.save_pin()
        self.assertTrue(has_pin(self.emp))

    def test_a_wrong_password_does_not_set_the_pin(self):
        response = self.save_pin(password="not-the-password")
        self.assertFalse(has_pin(self.emp))
        self.assertContains(response, "The password is not correct.")

    def test_pins_must_be_six_digits_and_match(self):
        self.save_pin(pin="12345", confirm="12345")
        self.assertFalse(has_pin(self.emp))
        self.save_pin(pin="123456", confirm="654321")
        self.assertFalse(has_pin(self.emp))

    def test_starting_needs_no_pin_on_an_approved_pc(self):
        from devices.start import request_approval

        own = make_device(self.emp, last_seen=timezone.now())
        other_employee = make_user("o@a.test", Role.EMPLOYEE, self.org)
        make_device(other_employee, last_seen=timezone.now())
        request_approval(self.emp, actor=make_user("ad@a.test", Role.ADMIN, self.org))  # the Admin approves first
        response = self.start()
        self.assertContains(response, "Monitoring is starting on your PC.")
        start = MonitoringStart.objects.get()
        self.assertEqual(start.device, own)
        self.assertEqual(start.status, MonitoringStart.Status.PENDING)

    def test_starting_without_a_connected_pc_says_so(self):
        response = self.start()
        self.assertContains(response, "No computer is connected to your account yet.")
        self.assertFalse(MonitoringStart.objects.exists())

    def test_an_admin_cannot_start_monitoring_for_an_employee(self):
        make_device(self.emp, last_seen=timezone.now())
        self.client.force_login(self.admin)
        self.start()
        self.assertFalse(MonitoringStart.objects.exists())

    def test_employee_cannot_start_without_admin_approval(self):
        make_device(self.emp, last_seen=timezone.now())
        response = self.start()
        self.assertContains(response, "has not approved monitoring yet")
        self.assertFalse(MonitoringStart.objects.exists())
