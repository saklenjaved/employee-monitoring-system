"""Search, filters, sorting and pages for the list screens.

A list view first limits its rows to what the user may see, then calls `build_listing`.
This module only narrows those rows further. It never adds rows back, so it cannot
widen what a user can see.

`rows` is either a queryset or a list of dicts (the Unclassified page uses a list).
"""
from collections import namedtuple
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import F, Q, QuerySet
from django.db.models.functions import Lower

PAGE_SIZE = 25
MAX_SEARCH_LENGTH = 100
ASC, DESC = "asc", "desc"
ARROWS = {ASC: "↑", DESC: "↓"}

# key: the name used in the URL for sorting, or None for a column that cannot be sorted.
# field: the database field (or the dict key for a list). text: sort ignoring upper and lower case.
Column = namedtuple("Column", "label key field text", defaults=(None, None, False))

# name: the URL parameter. choices: [(value, label)]. apply: function(rows, value) -> rows.
Filter = namedtuple("Filter", "name label choices apply")


def build_listing(
    request, rows, columns, search_fields=(), filters=(), default_sort=None, default_descending=False
):
    """Filter, search, sort and page `rows` using the options in the URL.

    Returns the context the list templates use, as `listing`.
    Unknown sort names, filter values and page numbers fall back to safe defaults.
    """
    sortable = {column.key: column for column in columns if column.key}
    sort = request.GET.get("sort")
    if sort not in sortable:
        sort = default_sort
    descending = (request.GET.get("dir") == DESC) if "dir" in request.GET else default_descending
    query = request.GET.get("q", "").strip()[:MAX_SEARCH_LENGTH]

    active_filters = []
    for option in filters:
        allowed = [value for value, _ in option.choices]
        selected = request.GET.get(option.name, "")
        selected = selected if selected in allowed else ""
        if selected:
            rows = option.apply(rows, selected)
        active_filters.append(
            {"name": option.name, "label": option.label, "choices": option.choices, "selected": selected}
        )

    rows = _search(rows, query, search_fields)
    if sort:
        rows = _sort(rows, sortable[sort], descending)

    base = {"q": query, **{option["name"]: option["selected"] for option in active_filters}}
    current_dir = DESC if descending else ASC
    headers = []
    for column in columns:
        if column.key is None:
            headers.append({"label": column.label, "link": "", "arrow": ""})
            continue
        is_current = column.key == sort
        next_dir = ASC if is_current and descending else DESC if is_current else ASC
        headers.append(
            {
                "label": column.label,
                "link": _link({**base, "sort": column.key, "dir": next_dir}),
                "arrow": ARROWS[current_dir] if is_current else "",
            }
        )

    return {
        "rows": rows,  # every row after search, filters and sorting, before paging
        "page": Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page")),
        "columns": headers,
        "filters": active_filters,
        "query": query,
        "sort": sort,
        "direction": current_dir,
        # Used by the pager and the sort links. Always holds the sort, so it is never empty.
        "querystring": _link({**base, "sort": sort, "dir": current_dir}),
        # True when a search or filter is set. Lets the template say "no results" instead of "none yet".
        "filtered": bool(query) or any(option["selected"] for option in active_filters),
    }


def _link(params):
    return urlencode({key: value for key, value in params.items() if value})


def _search(rows, query, fields):
    if not query or not fields:
        return rows
    if isinstance(rows, QuerySet):
        condition = Q()
        for field in fields:
            condition |= Q(**{f"{field}__icontains": query})
        return rows.filter(condition)
    needle = query.lower()
    return [row for row in rows if any(needle in str(row[field]).lower() for field in fields)]


def _sort(rows, column, descending):
    if isinstance(rows, QuerySet):
        expression = Lower(column.field) if column.text else F(column.field)
        order = expression.desc() if descending else expression.asc()
        return rows.order_by(order, "-pk" if descending else "pk")
    if column.text:
        return sorted(rows, key=lambda row: str(row[column.field]).lower(), reverse=descending)
    return sorted(rows, key=lambda row: row[column.field], reverse=descending)
