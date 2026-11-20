"""Admin pages for classification rules. Only an Admin may use them, and only for their own organization."""
from django.contrib import messages
from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from access_control.permissions import role_required
from accounts.models import Role
from common.listing import Column, Filter, build_listing
from workforce.services import teams_for

from . import services
from .forms import RuleForm
from .models import Category, ItemType

RULE_COLUMNS = (
    Column("Type", "type", "item_type", True),
    Column("Name", "name", "pattern", True),
    Column("Applies to", "applies_to", "team__name", True),
    Column("Current category", "category", "category", True),
    Column("Change category"),
)
WHOLE_ORGANIZATION = "org"


def filter_applies_to(rows, value):
    if value == WHOLE_ORGANIZATION:
        return rows.filter(team__isnull=True)
    return rows.filter(team_id=value)


@role_required(Role.ADMIN)
def rule_list(request):
    organization = request.user.organization
    form = RuleForm(organization, request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            services.create_rule(
                request.user, organization, data["item_type"], data["pattern"], data["category"], data["team"]
            )
        except IntegrityError:
            form.add_error(None, "Could not save because the rule already exists.")
        else:
            messages.success(request, "Rule created.")
            return redirect("rule_list")
    team_choices = [(str(team.pk), team.name) for team in teams_for(request.user)]
    listing = build_listing(
        request,
        services.rules_for(request.user),
        RULE_COLUMNS,
        search_fields=("pattern", "team__name"),
        filters=(
            Filter("item_type", "Type", ItemType.choices, lambda rows, value: rows.filter(item_type=value)),
            Filter("category", "Category", Category.choices, lambda rows, value: rows.filter(category=value)),
            Filter(
                "applies_to",
                "Applies to",
                [(WHOLE_ORGANIZATION, "Whole organization")] + team_choices,
                filter_applies_to,
            ),
        ),
        default_sort="name",
    )
    all_rules = services.rules_for(request.user)
    summary = {
        "total": all_rules.count(),
        "by_category": [
            {"value": value, "label": label, "count": all_rules.filter(category=value).count()}
            for value, label in Category.choices
        ],
    }
    context = {"listing": listing, "form": form, "categories": Category.choices, "summary": summary}
    return render(request, "productivity/rule_list.html", context)


@role_required(Role.ADMIN)
@require_POST
def rule_change(request, pk):
    rule = services.get_rule(request.user, pk)
    if rule is None:
        raise Http404
    category = request.POST.get("category")
    if category not in Category.values:
        messages.error(request, "Choose a category.")
    else:
        services.change_rule_category(request.user, rule, category)
        messages.success(request, "Rule updated.")
    return redirect("rule_list")


@role_required(Role.ADMIN)
@require_POST
def rule_delete(request, pk):
    rule = services.get_rule(request.user, pk)
    if rule is None:
        raise Http404
    services.delete_rule(request.user, rule)
    messages.success(request, "Rule deleted.")
    return redirect("rule_list")
