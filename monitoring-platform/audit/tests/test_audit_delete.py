"""Deleting audit entries: Super Admin only, single and bulk, with a permanent record of every deletion."""
from unittest import mock

from django.contrib.admin.sites import site as admin_site
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.models import Role, User
from accounts.tests.factories import make_org, make_super_admin, make_user
from audit import services
from audit.admin import AuditLogAdmin
from audit.models import AppendOnlyError, AuditDeletion, AuditLog
from audit.services import delete_entries, record_event
from organizations.models import Organization


class AuditDeleteBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.employee_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a)
        cls.admin_b = make_user("admin@b.test", Role.ADMIN, cls.org_b)
        cls.super_admin = make_super_admin()
        cls.url = reverse("audit_log_delete")
        cls.list_url = reverse("audit_log_list")

    def make_entries(self, count, organization, action="department.created"):
        return [record_event(self.admin_a, action, organization=organization) for _ in range(count)]


class DeleteServiceTests(AuditDeleteBase):
    def test_super_admin_deletes_chosen_entries_and_the_deletion_is_recorded(self):
        keep, drop1, drop2 = self.make_entries(3, self.org_a)
        deleted = delete_entries(self.super_admin, AuditLog.objects.filter(pk__in=[drop1.pk, drop2.pk]), "selected")
        self.assertEqual(deleted, 2)
        self.assertEqual(list(AuditLog.objects.values_list("pk", flat=True)), [keep.pk])
        record = AuditDeletion.objects.get()
        self.assertEqual(
            (record.actor, record.actor_email, record.entry_count, record.method),
            (self.super_admin, "super@platform.test", 2, "selected"),
        )
        self.assertIsNotNone(record.created_at)

    def test_only_the_super_admin_may_delete(self):
        entries = self.make_entries(2, self.org_a)
        for actor in (self.admin_a, self.employee_a, self.admin_b, None):
            with self.assertRaises(PermissionDenied):
                delete_entries(actor, AuditLog.objects.all(), "selected")
        self.assertEqual(AuditLog.objects.count(), 2)
        self.assertFalse(AuditDeletion.objects.exists())
        self.assertEqual(len(entries), 2)

    def test_deleting_audit_entries_never_touches_accounts_or_organizations(self):
        self.make_entries(3, self.org_a)
        users, organizations = User.objects.count(), Organization.objects.count()
        delete_entries(self.super_admin, AuditLog.objects.all(), "matching")
        self.assertEqual((User.objects.count(), Organization.objects.count()), (users, organizations))
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_a_large_deletion_runs_in_batches(self):
        self.make_entries(7, self.org_a)
        with mock.patch.object(services, "DELETE_BATCH_SIZE", 3):
            with self.assertNumQueries(1 + 3 + 1 + 2):  # read ids, 3 batch deletes, 1 savepoint pair, 1 record
                deleted = delete_entries(self.super_admin, AuditLog.objects.all(), "matching")
        self.assertEqual(deleted, 7)
        self.assertEqual(AuditDeletion.objects.get().entry_count, 7)

    def test_nothing_to_delete_leaves_no_record(self):
        self.assertEqual(delete_entries(self.super_admin, AuditLog.objects.none(), "selected"), 0)
        self.assertFalse(AuditDeletion.objects.exists())

    def test_the_deletion_record_survives_and_cannot_be_changed_or_deleted(self):
        self.make_entries(1, self.org_a)
        delete_entries(self.super_admin, AuditLog.objects.all(), "single")
        record = AuditDeletion.objects.get()
        record.entry_count = 99
        with self.assertRaises(AppendOnlyError):
            record.save()
        with self.assertRaises(AppendOnlyError):
            record.delete()
        with self.assertRaises(AppendOnlyError):
            AuditDeletion.objects.all().delete()
        with self.assertRaises(AppendOnlyError):
            AuditDeletion.objects.all().update(entry_count=1)

    def test_the_record_stays_readable_when_the_actor_account_is_removed(self):
        extra = User.objects.create_superuser(email="other-super@platform.test", password="a-long-test-password-1", full_name="Other")
        self.make_entries(1, self.org_a)
        delete_entries(extra, AuditLog.objects.all(), "single")
        extra.delete()
        record = AuditDeletion.objects.get()
        self.assertIsNone(record.actor)
        self.assertEqual(record.actor_email, "other-super@platform.test")

    def test_entries_still_cannot_be_deleted_any_other_way(self):
        entry = self.make_entries(1, self.org_a)[0]
        with self.assertRaises(AppendOnlyError):
            entry.delete()
        with self.assertRaises(AppendOnlyError):
            AuditLog.objects.all().delete()
        self.assertEqual(AuditLog.objects.count(), 1)


class DeleteViewTests(AuditDeleteBase):
    def post(self, user, data):
        self.client.force_login(user)
        return self.client.post(self.url, data)

    def test_super_admin_deletes_one_entry(self):
        first, second = self.make_entries(2, self.org_a)
        response = self.post(self.super_admin, {"single": first.pk})
        self.assertRedirects(response, self.list_url)
        self.assertEqual(list(AuditLog.objects.values_list("pk", flat=True)), [second.pk])
        self.assertEqual(AuditDeletion.objects.get().method, "single")

    def test_super_admin_deletes_ticked_entries_from_any_organization(self):
        a1, a2 = self.make_entries(2, self.org_a)
        b1 = record_event(self.admin_b, "department.created", organization=self.org_b)
        keep = record_event(self.admin_b, "team.created", organization=self.org_b)
        self.post(self.super_admin, {"entry": [a1.pk, b1.pk, a2.pk]})
        self.assertEqual(list(AuditLog.objects.values_list("pk", flat=True)), [keep.pk])
        record = AuditDeletion.objects.get()
        self.assertEqual((record.entry_count, record.method), (3, "selected"))

    def test_select_all_matching_deletes_every_page_of_the_filtered_entries(self):
        self.make_entries(30, self.org_a, action="department.created")  # more than one page of 25
        keep = record_event(self.admin_a, "team.created", organization=self.org_a)
        self.post(self.super_admin, {"select_matching": "1", "action": "department.created"})
        self.assertEqual(list(AuditLog.objects.values_list("pk", flat=True)), [keep.pk])
        record = AuditDeletion.objects.get()
        self.assertEqual((record.entry_count, record.method), (30, "matching"))

    def test_select_all_matching_respects_the_organization_filter(self):
        self.make_entries(2, self.org_a)
        self.make_entries(2, self.org_b)
        self.post(self.super_admin, {"select_matching": "1", "organization": str(self.org_a.pk)})
        self.assertEqual(set(AuditLog.objects.values_list("organization_id", flat=True)), {self.org_b.pk})

    def test_a_bad_date_range_never_widens_the_deletion(self):
        self.make_entries(2, self.org_a)
        response = self.post(self.super_admin, {"select_matching": "1", "from": "not-a-date"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AuditLog.objects.count(), 2)
        self.assertFalse(AuditDeletion.objects.exists())

    def test_nothing_selected_deletes_nothing(self):
        self.make_entries(2, self.org_a)
        response = self.post(self.super_admin, {}, )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AuditLog.objects.count(), 2)

    def test_unknown_or_bad_ids_delete_nothing(self):
        self.make_entries(1, self.org_a)
        self.post(self.super_admin, {"entry": ["999999", "abc", "-1"]})
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_the_return_link_keeps_the_filters(self):
        entry = self.make_entries(1, self.org_a)[0]
        response = self.post(self.super_admin, {"single": entry.pk, "q": "dept", "action": "department.created"})
        self.assertRedirects(response, f"{self.list_url}?q=dept&action=department.created")

    def test_an_admin_cannot_delete_even_their_own_organizations_entries(self):
        entries = self.make_entries(2, self.org_a)
        for data in ({"single": entries[0].pk}, {"entry": [entries[0].pk]}, {"select_matching": "1"}):
            self.assertEqual(self.post(self.admin_a, data).status_code, 403)
        self.assertEqual(AuditLog.objects.count(), 2)
        self.assertFalse(AuditDeletion.objects.exists())

    def test_an_admin_cannot_delete_another_organizations_entries(self):
        other = record_event(self.admin_b, "department.created", organization=self.org_b)
        self.assertEqual(self.post(self.admin_a, {"single": other.pk}).status_code, 403)
        self.assertEqual(self.post(self.admin_a, {"entry": [other.pk]}).status_code, 403)
        self.assertTrue(AuditLog.objects.filter(pk=other.pk).exists())

    def test_employees_and_anonymous_visitors_cannot_delete(self):
        entry = self.make_entries(1, self.org_a)[0]
        self.assertEqual(self.post(self.employee_a, {"single": entry.pk}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(self.url, {"single": entry.pk}).status_code, 302)  # to the login page
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_deleting_needs_a_post(self):
        self.client.force_login(self.super_admin)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_a_missing_csrf_token_is_refused(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.super_admin)
        entry = self.make_entries(1, self.org_a)[0]
        self.assertEqual(client.post(self.url, {"single": entry.pk}).status_code, 403)
        self.assertEqual(AuditLog.objects.count(), 1)


class AuditPageControlsTests(AuditDeleteBase):
    def test_the_super_admin_page_has_checkboxes_delete_buttons_and_history(self):
        self.make_entries(2, self.org_a)
        self.client.force_login(self.super_admin)
        response = self.client.get(self.list_url)
        for text in ('id="al-select-all"', 'name="entry"', "Delete selected", 'name="single"', "Deletion history", "data-confirm"):
            self.assertContains(response, text)
        self.assertContains(response, reverse("audit_log_delete"))

    def test_deletion_history_lists_who_when_and_how_many(self):
        self.make_entries(2, self.org_a)
        delete_entries(self.super_admin, AuditLog.objects.all(), "matching")
        self.client.force_login(self.super_admin)
        response = self.client.get(self.list_url)
        self.assertContains(response, "super@platform.test")
        self.assertContains(response, "matching")

    def test_an_admin_page_has_no_delete_controls_and_no_history(self):
        self.make_entries(2, self.org_a)
        delete_entries(self.super_admin, AuditLog.objects.all(), "matching")
        self.make_entries(1, self.org_a)
        self.client.force_login(self.admin_a)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        for text in ("al-select-all", 'name="entry"', "Delete selected", 'name="single"', "Deletion history", reverse("audit_log_delete")):
            self.assertNotContains(response, text)

    def test_an_admin_still_sees_only_their_own_organization(self):
        mine = record_event(self.admin_a, "department.created", organization=self.org_a)
        record_event(self.admin_b, "team.created", organization=self.org_b)
        self.client.force_login(self.admin_a)
        response = self.client.get(self.list_url)
        self.assertEqual([row.pk for row in response.context["listing"]["page"]], [mine.pk])

    def test_employees_cannot_open_the_page(self):
        self.client.force_login(self.employee_a)
        self.assertEqual(self.client.get(self.list_url).status_code, 403)


class DjangoAdminTests(AuditDeleteBase):
    def setUp(self):
        self.model_admin = AuditLogAdmin(AuditLog, admin_site)

    def request_for(self, user):
        request = RequestFactory().post("/")
        request.user = user
        return request

    def test_only_the_super_admin_has_delete_permission_in_the_admin_site(self):
        self.assertTrue(self.model_admin.has_delete_permission(self.request_for(self.super_admin)))
        self.assertFalse(self.model_admin.has_delete_permission(self.request_for(self.admin_a)))
        self.assertFalse(self.model_admin.has_add_permission(self.request_for(self.super_admin)))
        self.assertFalse(self.model_admin.has_change_permission(self.request_for(self.super_admin)))

    def test_deleting_in_the_admin_site_goes_through_the_service_and_is_recorded(self):
        entries = self.make_entries(3, self.org_a)
        self.model_admin.delete_model(self.request_for(self.super_admin), entries[0])
        self.model_admin.delete_queryset(self.request_for(self.super_admin), AuditLog.objects.filter(pk__in=[entries[1].pk, entries[2].pk]))
        self.assertEqual(AuditLog.objects.count(), 0)
        self.assertEqual(sorted(AuditDeletion.objects.values_list("method", "entry_count")), [("selected", 2), ("single", 1)])

    def test_the_admin_site_cannot_delete_for_anyone_else(self):
        entries = self.make_entries(1, self.org_a)
        with self.assertRaises(PermissionDenied):
            self.model_admin.delete_queryset(self.request_for(self.admin_a), AuditLog.objects.all())
        self.assertEqual(len(entries), AuditLog.objects.count())
