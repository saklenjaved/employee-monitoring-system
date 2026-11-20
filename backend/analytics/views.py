"""Admin page for items that have no classification rule yet."""
import json

from django.contrib import messages
from django.db import IntegrityError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from access_control.permissions import role_required
from accounts.models import Role
from common.listing import Column, Filter, build_listing
from productivity.forms import RuleForm
from productivity.models import Category, ItemType

from .services import UNCLASSIFIED_LIST_LIMIT, classify_item, unclassified_items

UNCLASSIFIED_COLUMNS = (
    Column("Type", "type", "item_type", True),
    Column("Name", "name", "name", True),
    Column("Time", "time", "seconds"),
    Column("Employees", "employees", "employees"),
    Column("Classify as"),
)


@role_required(Role.ADMIN)
def unclassified_list(request):
    items = unclassified_items(request.user.organization, limit=UNCLASSIFIED_LIST_LIMIT)
    listing = build_listing(
        request,
        items,
        UNCLASSIFIED_COLUMNS,
        search_fields=("name",),
        filters=(
            Filter(
                "item_type",
                "Type",
                ItemType.choices,
                lambda rows, value: [row for row in rows if row["item_type"] == value],
            ),
        ),
        default_sort="time",
        default_descending=True,
    )
    summary = {"count": len(items), "seconds": sum(item["seconds"] for item in items)}
    return render(
        request,
        "analytics/unclassified.html",
        {"listing": listing, "categories": Category.choices, "summary": summary, "item_limit": UNCLASSIFIED_LIST_LIMIT},
    )


@role_required(Role.ADMIN)
@require_POST
def unclassified_classify(request):
    organization = request.user.organization
    form = RuleForm(
        organization,
        {
            "item_type": request.POST.get("item_type", ""),
            "pattern": request.POST.get("name", ""),
            "category": request.POST.get("category", ""),
        },
    )
    if form.is_valid():
        data = form.cleaned_data
        try:
            classify_item(request.user, organization, data["item_type"], data["pattern"], data["category"])
        except IntegrityError:
            ok, text = False, "A rule for this item already exists."
        else:
            ok, text = True, f"{data['pattern']} classified as {data['category']}."
    else:
        ok, text = False, "Could not classify that item. Check the category and try again."
    if request.headers.get("HX-Request"):
        return _classify_result(ok, text)
    (messages.success if ok else messages.error)(request, text)
    return redirect("unclassified_list")


def _classify_result(ok, text):
    """For the in-place Save: an empty reply removes the row. A failure keeps the row (HX-Reswap none)."""
    response = HttpResponse("")
    trigger = {"toast": {"message": text, "tags": "success" if ok else "error"}}
    if ok:
        trigger["refreshList"] = True  # the list reloads its summary and the remaining rows
    else:
        response["HX-Reswap"] = "none"
    response["HX-Trigger"] = json.dumps(trigger)
    return response
