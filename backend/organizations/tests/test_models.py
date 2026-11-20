from django.db import IntegrityError, transaction
from django.test import TestCase

from organizations.models import Organization


class OrganizationTests(TestCase):
    def test_new_organization_is_active_by_default(self):
        org = Organization.objects.create(name="Acme")
        self.assertTrue(org.is_active)
        self.assertEqual(str(org), "Acme")

    def test_duplicate_name_is_rejected(self):
        Organization.objects.create(name="Acme")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Organization.objects.create(name="Acme")
