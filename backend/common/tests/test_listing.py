from django.test import RequestFactory, SimpleTestCase, TestCase

from common.listing import PAGE_SIZE, Column, Filter, build_listing
from organizations.models import Organization

LIST_KIND_FILTER = Filter(
    "kind", "Kind", [("a", "A"), ("b", "B")], lambda rows, value: [row for row in rows if row["kind"] == value]
)


def request_for(query_string=""):
    return RequestFactory().get(f"/list/?{query_string}")


def names(listing):
    return [org.name for org in listing["page"]]


class QuerysetListingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name in ("beta", "Alpha", "gamma"):
            Organization.objects.create(name=name)

    def listing(self, query_string="", **options):
        return build_listing(
            request_for(query_string),
            Organization.objects.all(),
            (Column("Name", "name", "name", True), Column("")),
            search_fields=("name",),
            default_sort="name",
            **options,
        )

    def test_sorts_by_name_ignoring_case(self):
        self.assertEqual(names(self.listing()), ["Alpha", "beta", "gamma"])

    def test_descending_reverses_the_order(self):
        self.assertEqual(names(self.listing("sort=name&dir=desc")), ["gamma", "beta", "Alpha"])

    def test_an_unknown_sort_name_falls_back_to_the_default(self):
        self.assertEqual(names(self.listing("sort=name__drop")), ["Alpha", "beta", "gamma"])

    def test_search_matches_part_of_a_name_in_any_case(self):
        self.assertEqual(names(self.listing("q=AMM")), ["gamma"])

    def test_no_match_is_reported_as_filtered_with_nothing_on_the_page(self):
        listing = self.listing("q=zzz")
        self.assertEqual(names(listing), [])
        self.assertTrue(listing["filtered"])

    def test_a_blank_search_is_not_a_filter(self):
        listing = self.listing("q=%20%20")
        self.assertFalse(listing["filtered"])
        self.assertEqual(listing["query"], "")

    def test_a_long_search_is_cut_to_100_characters(self):
        self.assertEqual(len(self.listing("q=" + "a" * 150)["query"]), 100)

    def test_the_querystring_keeps_search_and_sort_but_not_the_page(self):
        listing = self.listing("q=alp&sort=name&dir=desc&page=2")
        self.assertEqual(listing["querystring"], "q=alp&sort=name&dir=desc")

    def test_header_link_flips_the_direction_of_the_sorted_column(self):
        first = self.listing()["columns"][0]
        self.assertEqual((first["link"], first["arrow"]), ("sort=name&dir=desc", "↑"))
        flipped = self.listing("sort=name&dir=desc")["columns"][0]
        self.assertEqual((flipped["link"], flipped["arrow"]), ("sort=name&dir=asc", "↓"))

    def test_a_column_without_a_key_has_no_link(self):
        self.assertEqual(self.listing()["columns"][1], {"label": "", "link": "", "arrow": ""})


class PagingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for number in range(PAGE_SIZE + 3):
            Organization.objects.create(name=f"org {number:02d}")

    def listing(self, query_string=""):
        return build_listing(
            request_for(query_string),
            Organization.objects.all(),
            (Column("Name", "name", "name", True),),
            default_sort="name",
        )

    def test_a_page_holds_25_rows_and_the_rest_go_on_the_next_page(self):
        first, second = self.listing(), self.listing("page=2")
        self.assertEqual(len(first["page"]), PAGE_SIZE)
        self.assertEqual(len(second["page"]), 3)
        self.assertEqual(first["page"].paginator.count, PAGE_SIZE + 3)

    def test_a_page_number_that_is_not_a_number_shows_the_first_page(self):
        self.assertEqual(self.listing("page=abc")["page"].number, 1)

    def test_a_page_past_the_end_shows_the_last_page(self):
        self.assertEqual(self.listing("page=999")["page"].number, 2)


class FilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Organization.objects.create(name="One")
        Organization.objects.create(name="Two")

    def listing(self, query_string):
        # The filter is applied by name, so it is shown with the organizations' own rows here.
        rows = Organization.objects.all()
        filters = (Filter("kind", "Kind", [("a", "A")], lambda queryset, value: queryset.filter(name="One")),)
        return build_listing(
            request_for(query_string), rows, (Column("Name", "name", "name", True),), filters=filters, default_sort="name"
        )

    def test_a_chosen_filter_narrows_the_rows(self):
        listing = self.listing("kind=a")
        self.assertEqual(names(listing), ["One"])
        self.assertTrue(listing["filtered"])
        self.assertEqual(listing["filters"][0]["selected"], "a")

    def test_a_value_that_is_not_a_choice_is_ignored(self):
        listing = self.listing("kind=drop")
        self.assertEqual(names(listing), ["One", "Two"])
        self.assertFalse(listing["filtered"])
        self.assertEqual(listing["filters"][0]["selected"], "")

    def test_the_filter_stays_in_the_querystring_for_the_pager(self):
        self.assertIn("kind=a", self.listing("kind=a")["querystring"])


class ListRowsTests(SimpleTestCase):
    ROWS = [
        {"name": "beta.exe", "kind": "b", "seconds": 30},
        {"name": "alpha.exe", "kind": "a", "seconds": 300},
        {"name": "gamma.exe", "kind": "a", "seconds": 10},
    ]

    def listing(self, query_string="", **options):
        return build_listing(
            request_for(query_string),
            list(self.ROWS),
            (Column("Name", "name", "name", True), Column("Time", "time", "seconds"), Column("Kind", "kind", "kind", True)),
            search_fields=("name",),
            filters=(LIST_KIND_FILTER,),
            default_sort="time",
            **options,
        )

    def test_rows_sort_by_their_value(self):
        self.assertEqual([row["name"] for row in self.listing(default_descending=True)["page"]],
                         ["alpha.exe", "beta.exe", "gamma.exe"])

    def test_default_descending_puts_the_largest_first(self):
        self.assertEqual(self.listing("sort=time", default_descending=True)["page"][0]["name"], "alpha.exe")

    def test_search_and_filter_work_on_rows(self):
        listing = self.listing("q=GAM&kind=a")
        self.assertEqual([row["name"] for row in listing["page"]], ["gamma.exe"])

    def test_no_rows_at_all_is_not_filtered(self):
        listing = build_listing(request_for(), [], (Column("Name", "name", "name", True),), default_sort="name")
        self.assertEqual(len(listing["page"]), 0)
        self.assertFalse(listing["filtered"])
