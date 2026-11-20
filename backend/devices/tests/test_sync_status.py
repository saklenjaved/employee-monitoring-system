"""The employee's sync status: what the latest heartbeat says about the agent on their PC."""
from datetime import timedelta

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from devices.services import sync_status
from devices.tests.test_setup import make_device


class SyncStatusTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org)

    def test_no_pc_means_not_connected(self):
        self.assertEqual(sync_status(self.emp), {"state": "not_connected"})

    def test_a_seen_pc_with_nothing_queued_is_up_to_date(self):
        make_device(self.emp, last_seen=timezone.now())
        self.assertEqual(sync_status(self.emp)["state"], "up_to_date")

    def test_queued_items_mean_the_agent_is_uploading(self):
        device = make_device(self.emp, last_seen=timezone.now())
        device.queue_depth = 4
        device.save(update_fields=["queue_depth"])
        status = sync_status(self.emp)
        self.assertEqual((status["state"], status["queue_depth"]), ("uploading", 4))

    def test_an_old_heartbeat_is_offline_and_keeps_the_queue_count(self):
        last_seen = timezone.now() - timedelta(seconds=settings.DEVICE_ONLINE_SECONDS + 60)
        device = make_device(self.emp, last_seen=last_seen)
        device.queue_depth = 2
        device.save(update_fields=["queue_depth"])
        status = sync_status(self.emp)
        self.assertEqual((status["state"], status["queue_depth"]), ("offline", 2))

    def test_a_revoked_pc_does_not_count(self):
        make_device(self.emp, last_seen=timezone.now(), active=False)
        self.assertEqual(sync_status(self.emp), {"state": "not_connected"})

    def test_the_employee_page_shows_the_status(self):
        make_device(self.emp, last_seen=timezone.now())
        self.client.force_login(self.emp)
        response = self.client.get("/dashboard/me/")
        self.assertContains(response, "Agent running.")
        self.assertContains(response, "Everything is uploaded.")
