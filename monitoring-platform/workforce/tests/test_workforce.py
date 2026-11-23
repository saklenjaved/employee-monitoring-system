import re

from django.core import mail
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.models import Role, User
from accounts.services import activate_membership
from accounts.tests.factories import make_org, make_super_admin, make_user
from audit.models import AuditLog
from workforce import services
from workforce.models import Department, EmployeeProfile, Team, UserTeam


class ScopeTests(TestCase):
    """Who can see which people. Another organization is always invisible."""

    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.manager_a = make_user("manager@a.test", Role.MANAGER, cls.org_a)
        cls.other_manager_a = make_user("manager2@a.test", Role.MANAGER, cls.org_a)
        cls.emp_a1 = make_user("e1@a.test", Role.EMPLOYEE, cls.org_a)
        cls.emp_a2 = make_user("e2@a.test", Role.EMPLOYEE, cls.org_a)
        cls.admin_b = make_user("admin@b.test", Role.ADMIN, cls.org_b)
        cls.emp_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b)
        cls.super_admin = make_super_admin()
        cls.team_a = Team.objects.create(organization=cls.org_a, name="Support")
        cls.team_b = Team.objects.create(organization=cls.org_b, name="Support")
        for user in (cls.manager_a, cls.emp_a1):
            UserTeam.objects.create(organization=cls.org_a, team=cls.team_a, user=user)

    def test_admin_manages_only_own_organizations_people(self):
        people = set(services.people_for(self.admin_a))
        self.assertEqual(people, {self.manager_a, self.other_manager_a, self.emp_a1, self.emp_a2})

    def test_admin_cannot_get_person_or_team_of_another_organization(self):
        self.assertIsNone(services.get_person(self.admin_a, self.emp_b.pk))
        self.assertIsNone(services.get_team(self.admin_a, self.team_b.pk))
        self.assertEqual(services.get_team(self.admin_a, self.team_a.pk), self.team_a)

    def test_other_roles_manage_nobody(self):
        for user in (self.manager_a, self.emp_a1, self.super_admin):
            with self.subTest(role=user.role):
                self.assertEqual(list(services.people_for(user)), [])
                self.assertEqual(list(services.teams_for(user)), [])

    def test_visible_employees_for_admin_is_whole_organization(self):
        self.assertEqual(set(services.visible_employees(self.admin_a)), {self.emp_a1, self.emp_a2})

    def test_manager_sees_only_employees_of_managed_teams(self):
        self.assertEqual(list(services.visible_employees(self.manager_a)), [self.emp_a1])

    def test_manager_without_teams_sees_nobody(self):
        self.assertEqual(list(services.visible_employees(self.other_manager_a)), [])

    def test_employee_sees_only_self(self):
        self.assertEqual(list(services.visible_employees(self.emp_a2)), [self.emp_a2])

    def test_super_admin_sees_no_employees(self):
        self.assertEqual(list(services.visible_employees(self.super_admin)), [])

    def test_web_pages_hide_other_organizations(self):
        self.client.force_login(self.admin_a)
        self.assertEqual(self.client.get(reverse("person_detail", args=[self.emp_b.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("team_edit", args=[self.team_b.pk])).status_code, 404)
        response = self.client.post(reverse("person_set_active", args=[self.emp_b.pk]), {"active": "0"})
        self.assertEqual(response.status_code, 404)
        self.emp_b.refresh_from_db()
        self.assertTrue(self.emp_b.is_active)
        listing = self.client.get(reverse("person_list"))
        self.assertContains(listing, "e1@a.test")
        self.assertNotContains(listing, "e@b.test")

    def test_only_admin_may_open_workforce_pages(self):
        urls = [reverse(name) for name in ("person_list", "team_list", "department_list", "person_create", "team_create")]
        for user in (self.manager_a, self.emp_a1, self.super_admin):
            self.client.force_login(user)
            for url in urls:
                with self.subTest(role=user.role, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(reverse("person_list"))
        self.assertEqual(response.status_code, 302)

    def test_other_roles_cannot_change_people_teams_or_devices(self):
        urls = [
            ("post", reverse("person_set_active", args=[self.emp_a2.pk]), {"active": "0"}),
            ("get", reverse("team_edit", args=[self.team_a.pk]), None),
            ("post", reverse("team_edit", args=[self.team_a.pk]), {"name": "Hacked"}),
            ("post", reverse("department_list"), {"name": "Hacked"}),
        ]
        for user in (self.manager_a, self.emp_a1, self.super_admin):
            self.client.force_login(user)
            for method, url, data in urls:
                with self.subTest(role=user.role, url=url):
                    response = getattr(self.client, method)(url, data) if data else getattr(self.client, method)(url)
                    self.assertEqual(response.status_code, 403)
        self.emp_a2.refresh_from_db()
        self.team_a.refresh_from_db()
        self.assertTrue(self.emp_a2.is_active)
        self.assertEqual(self.team_a.name, "Support")


class DepartmentAndTeamTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.manager_a = make_user("manager@a.test", Role.MANAGER, cls.org_a)
        cls.emp_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a)
        cls.emp_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b)
        cls.manager_b = make_user("manager@b.test", Role.MANAGER, cls.org_b)
        cls.dept_b = Department.objects.create(organization=cls.org_b, name="Sales")

    def setUp(self):
        self.client.force_login(self.admin_a)

    def test_create_department_is_audited(self):
        self.client.post(reverse("department_list"), {"name": "Support"})
        department = Department.objects.get(name="Support")
        self.assertEqual(department.organization, self.org_a)
        self.assertTrue(AuditLog.objects.filter(action="department.created", organization=self.org_a).exists())

    def test_department_name_is_unique_per_organization_ignoring_case(self):
        self.client.post(reverse("department_list"), {"name": "Support"})
        self.client.post(reverse("department_list"), {"name": "support"})
        self.assertEqual(Department.objects.filter(organization=self.org_a).count(), 1)

    def test_same_department_name_is_allowed_in_another_organization(self):
        self.client.post(reverse("department_list"), {"name": "Sales"})
        self.assertEqual(Department.objects.filter(name="Sales").count(), 2)

    def test_create_team_with_manager_and_employee(self):
        response = self.client.post(
            reverse("team_create"),
            {"name": "Support", "managers": [self.manager_a.pk], "employees": [self.emp_a.pk]},
        )
        self.assertRedirects(response, reverse("team_list"))
        team = Team.objects.get(name="Support")
        self.assertEqual(team.organization, self.org_a)
        memberships = UserTeam.objects.filter(team=team)
        self.assertEqual({m.user_id for m in memberships}, {self.manager_a.pk, self.emp_a.pk})
        self.assertTrue(all(m.organization_id == self.org_a.pk for m in memberships))

    def test_team_cannot_use_people_or_departments_of_another_organization(self):
        for field, value in (
            ("employees", [self.emp_b.pk]),
            ("managers", [self.manager_b.pk]),
            ("department", self.dept_b.pk),
        ):
            with self.subTest(field=field):
                response = self.client.post(reverse("team_create"), {"name": f"T-{field}", field: value})
                self.assertEqual(response.status_code, 200)
        self.assertFalse(Team.objects.filter(organization=self.org_a).exists())

    def test_employee_cannot_be_chosen_as_manager(self):
        response = self.client.post(reverse("team_create"), {"name": "T", "managers": [self.emp_a.pk]})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Team.objects.exists())

    def test_editing_a_team_replaces_its_members(self):
        team = services.create_team(self.admin_a, self.org_a, "Support", None)
        services.update_team(self.admin_a, team, "Support", None, [self.manager_a], [self.emp_a])
        self.client.post(reverse("team_edit", args=[team.pk]), {"name": "Support 2", "employees": [self.emp_a.pk]})
        team.refresh_from_db()
        self.assertEqual(team.name, "Support 2")
        self.assertEqual({m.user_id for m in team.memberships.all()}, {self.emp_a.pk})

    def test_team_audit_notes_list_added_and_removed_user_ids(self):
        team = services.create_team(self.admin_a, self.org_a, "Support", None, [self.manager_a], [self.emp_a])
        created = AuditLog.objects.get(action="team.created")
        self.assertIn(str(self.manager_a.pk), created.detail)
        services.update_team(self.admin_a, team, "Support", None, [], [self.emp_a])
        updated = AuditLog.objects.get(action="team.updated")
        self.assertEqual(updated.detail, f"added: -; removed: {self.manager_a.pk}")

    def test_a_failed_team_creation_leaves_nothing_behind(self):
        with self.assertRaises(ValueError):
            services.create_team(self.admin_a, self.org_a, "Support", None, [], [self.emp_b])
        self.assertFalse(Team.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action="team.created").exists())

    def test_editing_a_team_keeps_members_who_were_deactivated(self):
        team = services.create_team(self.admin_a, self.org_a, "Support", None, [], [self.emp_a])
        self.emp_a.is_active = False
        self.emp_a.save()
        self.client.post(reverse("team_edit", args=[team.pk]), {"name": "Support", "employees": [self.emp_a.pk]})
        self.assertTrue(team.memberships.filter(user=self.emp_a).exists())

    def test_service_rejects_user_from_another_organization(self):
        team = services.create_team(self.admin_a, self.org_a, "Support", None)
        with self.assertRaises(ValueError):
            services.update_team(self.admin_a, team, "Support", None, [], [self.emp_b])
        self.assertFalse(team.memberships.exists())


class PersonTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.admin_b = make_user("admin@b.test", Role.ADMIN, cls.org_b)
        cls.dept_a = Department.objects.create(organization=cls.org_a, name="Support")
        cls.dept_b = Department.objects.create(organization=cls.org_b, name="Sales")

    def setUp(self):
        self.client.force_login(self.admin_a)

    def create(self, **overrides):
        data = {"email": "New@A.test", "full_name": "New Person", "role": "employee", "employee_code": "E-1"}
        data.update(overrides)
        return self.client.post(reverse("person_create"), data)

    def activate_new_person(self, email="new@a.test"):
        """Activate the invitation the way the employee does: with the code from the email."""
        code = re.search(r"\b(\d{8})\b", mail.outbox[-1].body).group(1)
        user, error = activate_membership(RequestFactory().get("/"), email, code, "a-long-new-password-2")
        self.assertEqual(error, "")
        return user

    def test_create_employee_makes_account_profile_email_and_audit(self):
        response = self.create(department=self.dept_a.pk)
        user = User.objects.get(email="new@a.test")
        self.assertRedirects(response, reverse("person_detail", args=[user.pk]))
        self.assertEqual(user.role, Role.EMPLOYEE)
        self.assertEqual(user.organization, self.org_a)
        profile = EmployeeProfile.objects.get(user=user, organization=self.org_a)
        self.assertEqual(profile.employee_code, "E-1")
        self.assertEqual(profile.department, self.dept_a)
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(AuditLog.objects.filter(action="membership.invited", organization=self.org_a).exists())

    def test_the_new_account_belongs_to_the_admins_organization_even_if_a_form_field_says_otherwise(self):
        self.create(organization=self.org_b.pk, organization_id=self.org_b.pk)
        self.assertEqual(User.objects.get(email="new@a.test").organization, self.org_a)

    def test_roles_other_than_employee_and_manager_are_refused(self):
        for role in ("admin", "super_admin"):
            with self.subTest(role=role):
                response = self.create(role=role, email=f"new-{role}@a.test")
                self.assertEqual(response.status_code, 200)
                self.assertFalse(User.objects.filter(email=f"new-{role}@a.test").exists())

    def test_department_of_another_organization_is_refused(self):
        response = self.create(department=self.dept_b.pk)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="new@a.test").exists())

    def test_duplicate_email_is_refused(self):
        self.create()
        response = self.create(employee_code="E-2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(email="new@a.test").count(), 1)

    def test_an_email_used_in_another_organization_gets_a_separate_account_here(self):
        response = self.create(email="admin@b.test")  # exists in organization B
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.filter(email="admin@b.test").count(), 2)

    def test_a_duplicate_email_in_this_organization_says_so(self):
        self.create()
        response = self.create(employee_code="E-2")
        self.assertContains(response, "A person with this email already exists in your organization.")

    def test_employee_code_is_unique_inside_one_organization_only(self):
        self.create()
        self.create(email="two@a.test")  # same code E-1 in the same organization
        self.assertFalse(User.objects.filter(email="two@a.test").exists())
        self.client.force_login(self.admin_b)
        self.create(email="three@b.test")  # same code in another organization is fine
        self.assertTrue(EmployeeProfile.objects.filter(organization=self.org_b, employee_code="E-1").exists())

    def test_edit_person(self):
        self.create()
        user = User.objects.get(email="new@a.test")
        self.client.post(
            reverse("person_detail", args=[user.pk]),
            {"full_name": "Renamed", "employee_code": "E-9", "department": self.dept_a.pk},
        )
        user.refresh_from_db()
        self.assertEqual(user.full_name, "Renamed")
        self.assertEqual(EmployeeProfile.objects.get(user=user, organization=self.org_a).employee_code, "E-9")
        self.assertTrue(AuditLog.objects.filter(action="employee.updated").exists())

    def test_deactivate_and_activate_person_with_audit(self):
        self.create()
        self.activate_new_person()
        user = User.objects.get(email="new@a.test")
        url = reverse("person_set_active", args=[user.pk])
        self.client.post(url, {"active": "0"})
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertTrue(AuditLog.objects.filter(action="user.deactivated", target_id=str(user.pk)).exists())
        self.client.post(url, {"active": "1"})
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_admin_cannot_deactivate_themselves(self):
        with self.assertRaises(ValueError):
            services.set_person_active(self.admin_a, self.admin_a, False)
