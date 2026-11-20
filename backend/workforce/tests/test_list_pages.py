"""The People, Teams and Departments lists: search, filters, sorting, paging and empty states.

Every list is limited to the Admin's organization, so another organization's names never appear,
even when someone searches for them.
"""
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from accounts.tests.factories import make_org, make_user
from common.listing import PAGE_SIZE
from workforce.models import Department, EmployeeProfile, Team


def names_on_page(response):
    return [person.full_name for person in response.context["listing"]["page"]]


class PeopleListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a, full_name="Ada Admin")
        cls.erin = make_user("erin@a.test", Role.EMPLOYEE, cls.org_a, full_name="Erin Adams")
        cls.mo = make_user("mo@a.test", Role.MANAGER, cls.org_a, full_name="Mo Brown")
        cls.zed = make_user("zed@a.test", Role.EMPLOYEE, cls.org_a, full_name="Zed Zulu")
        cls.zed.is_active = False
        cls.zed.save(update_fields=["is_active"])
        cls.sales = Department.objects.create(organization=cls.org_a, name="Sales")
        cls.secret_b = Department.objects.create(organization=cls.org_b, name="Secret B")
        EmployeeProfile.objects.create(user=cls.erin, organization=cls.org_a, department=cls.sales, employee_code="E-100")
        EmployeeProfile.objects.create(user=cls.mo, organization=cls.org_a, department=cls.sales)
        cls.olga = make_user("olga@b.test", Role.EMPLOYEE, cls.org_b, full_name="Olga Other")
        EmployeeProfile.objects.create(user=cls.olga, organization=cls.org_b, department=cls.secret_b)

    def setUp(self):
        self.client.force_login(self.admin_a)
        self.url = reverse("person_list")

    def get(self, query_string=""):
        return self.client.get(f"{self.url}?{query_string}")

    def test_the_list_shows_the_organizations_people_by_name(self):
        self.assertEqual(names_on_page(self.get()), ["Erin Adams", "Mo Brown", "Zed Zulu"])

    def test_search_finds_a_person_by_name_email_or_employee_code(self):
        self.assertEqual(names_on_page(self.get("q=adams")), ["Erin Adams"])
        self.assertEqual(names_on_page(self.get("q=mo@a.test")), ["Mo Brown"])
        self.assertEqual(names_on_page(self.get("q=e-100")), ["Erin Adams"])

    def test_another_organizations_person_is_never_found(self):
        response = self.get("q=Olga")
        self.assertEqual(names_on_page(response), [])
        # The search box repeats the typed word, so check the full name and email instead.
        self.assertNotContains(response, "Olga Other")
        self.assertNotContains(response, "olga@b.test")
        self.assertContains(response, "No people match your search or filters.")

    def test_role_filter(self):
        self.assertEqual(names_on_page(self.get("role=manager")), ["Mo Brown"])
        self.assertEqual(names_on_page(self.get("role=employee")), ["Erin Adams", "Zed Zulu"])

    def test_status_filter(self):
        self.assertEqual(names_on_page(self.get("status=deactivated")), ["Zed Zulu"])
        self.assertEqual(names_on_page(self.get("status=active")), ["Erin Adams", "Mo Brown"])

    def test_department_filter_uses_only_this_organizations_departments(self):
        self.assertEqual(names_on_page(self.get(f"department={self.sales.pk}")), ["Erin Adams", "Mo Brown"])
        # A department of another organization is not a valid choice, so it filters nothing.
        self.assertEqual(names_on_page(self.get(f"department={self.secret_b.pk}")), ["Erin Adams", "Mo Brown", "Zed Zulu"])

    def test_sorting_by_name_in_reverse(self):
        self.assertEqual(names_on_page(self.get("sort=name&dir=desc")), ["Zed Zulu", "Mo Brown", "Erin Adams"])

    def test_an_unknown_sort_is_ignored(self):
        self.assertEqual(names_on_page(self.get("sort=password")), ["Erin Adams", "Mo Brown", "Zed Zulu"])

    def test_empty_organization_says_there_are_no_people_yet(self):
        empty_org = make_org("Empty")
        self.client.force_login(make_user("admin@empty.test", Role.ADMIN, empty_org))
        response = self.get()
        self.assertContains(response, "No people yet.")
        self.assertNotContains(response, "Clear search and filters")

    def test_clear_link_appears_only_when_a_search_or_filter_is_set(self):
        self.assertNotContains(self.get(), ">Clear</a>")
        self.assertContains(self.get("role=manager"), ">Clear</a>")

    def test_search_text_is_escaped_in_the_page(self):
        self.assertContains(self.get("q=%3Cscript%3E"), "&lt;script&gt;")
        # The search box must hold the escaped text, never the raw tag (the page itself has its own script tags).
        self.assertNotContains(self.get("q=%3Cscript%3E"), 'value="<script>"')

    def test_other_roles_cannot_open_the_list(self):
        for user in (self.mo, self.erin):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                self.assertEqual(self.get().status_code, 403)


class PeoplePagingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Big")
        cls.admin = make_user("admin@big.test", Role.ADMIN, cls.org, full_name="Big Admin")
        for number in range(PAGE_SIZE + 2):
            make_user(f"e{number:02d}@big.test", Role.EMPLOYEE, cls.org, full_name=f"Person {number:02d}")

    def setUp(self):
        self.client.force_login(self.admin)

    def test_long_lists_are_split_into_pages_with_links_that_keep_the_search(self):
        url = reverse("person_list")
        first = self.client.get(url, {"q": "person"})
        self.assertEqual(len(first.context["listing"]["page"]), PAGE_SIZE)
        self.assertContains(first, "Showing 1 to 25 of 27.")
        self.assertContains(first, "?q=person&amp;sort=name&amp;dir=asc&amp;page=2")
        second = self.client.get(url, {"q": "person", "page": 2})
        self.assertEqual(len(second.context["listing"]["page"]), 2)


class TeamAndDepartmentListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.sales = Department.objects.create(organization=cls.org_a, name="Sales")
        cls.ops = Department.objects.create(organization=cls.org_a, name="Operations")
        Team.objects.create(organization=cls.org_a, name="North", department=cls.sales)
        Team.objects.create(organization=cls.org_a, name="South", department=cls.ops)
        Team.objects.create(organization=cls.org_a, name="Floating")
        Team.objects.create(organization=cls.org_b, name="Rival Team")
        Department.objects.create(organization=cls.org_b, name="Rival Dept")
        cls.empty_org = make_org("Empty")
        cls.empty_admin = make_user("admin@empty.test", Role.ADMIN, cls.empty_org)

    def setUp(self):
        self.client.force_login(self.admin_a)

    def team_names(self, query_string=""):
        response = self.client.get(f"{reverse('team_list')}?{query_string}")
        return [team.name for team in response.context["listing"]["page"]]

    def test_teams_are_sorted_by_name_and_limited_to_the_organization(self):
        self.assertEqual(self.team_names(), ["Floating", "North", "South"])

    def test_team_search_matches_the_department_name(self):
        self.assertEqual(self.team_names("q=operations"), ["South"])

    def test_team_department_filter(self):
        self.assertEqual(self.team_names(f"department={self.sales.pk}"), ["North"])

    def test_team_search_never_finds_another_organization(self):
        response = self.client.get(reverse("team_list"), {"q": "Rival"})
        self.assertNotContains(response, "Rival Team")
        self.assertContains(response, "No teams match your search or filters.")

    def test_empty_team_list_says_there_are_no_teams_yet(self):
        self.client.force_login(self.empty_admin)
        self.assertContains(self.client.get(reverse("team_list")), "No teams yet.")

    def test_department_search_and_empty_state(self):
        response = self.client.get(reverse("department_list"), {"q": "sales"})
        self.assertEqual([d.name for d in response.context["listing"]["page"]], ["Sales"])
        self.assertNotContains(self.client.get(reverse("department_list"), {"q": "rival"}), "Rival Dept")
        self.client.force_login(self.empty_admin)
        self.assertContains(self.client.get(reverse("department_list")), "No departments yet.")

    def test_the_department_form_still_saves_and_the_list_still_shows_it(self):
        response = self.client.post(reverse("department_list"), {"name": "Finance"}, follow=True)
        self.assertContains(response, "Finance")
