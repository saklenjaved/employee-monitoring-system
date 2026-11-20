"""Classification rules and how a rule is chosen for an item.

Choosing a rule for an employee:
1. Rules of the employee's teams that match. If an employee is in several teams with
   matching rules, the most specific match wins, then the oldest rule (default, to confirm).
2. Otherwise the organization-wide rules that match, again the most specific match.
3. Otherwise the item is "unclassified".
"""
from django.db import transaction

from accounts.models import Role
from audit.services import record_event

from .models import UNCLASSIFIED, ClassificationRule, ItemType


class _RuleSet:
    """The rules of one scope (the whole organization, or one team), ready for fast lookup.

    A scope holds at most one rule per item, so a lookup is a dictionary read.
    """

    def __init__(self, rules):
        self._by_item = {(rule.item_type, rule.pattern): rule for rule in rules}

    def best(self, item_type, name):
        """The rule for an item, or None. An application matches by exact name. A website matches
        its domain and every parent domain, and the longest (most specific) match wins."""
        if item_type == ItemType.APP:
            return self._by_item.get((item_type, name))
        labels = name.split(".")
        for start in range(len(labels)):
            rule = self._by_item.get((item_type, ".".join(labels[start:])))
            if rule is not None:
                return rule
        return None


class Classifier:
    """Looks up the category of an item for an employee. Load it once, use it many times."""

    def __init__(self, organization):
        rules = list(ClassificationRule.objects.filter(organization=organization))
        self._org_wide = _RuleSet(rule for rule in rules if rule.team_id is None)
        teams = {rule.team_id for rule in rules if rule.team_id is not None}
        self._by_team = {team: _RuleSet(rule for rule in rules if rule.team_id == team) for team in teams}

    def category_for(self, item_type, name, team_ids=()):
        name = name.lower()
        team_matches = [
            rule
            for team_id in team_ids
            if team_id in self._by_team
            for rule in [self._by_team[team_id].best(item_type, name)]
            if rule is not None
        ]
        if team_matches:
            # Several teams: the most specific match wins, then the oldest rule.
            rule = min(team_matches, key=lambda rule: (-len(rule.pattern), rule.id))
        else:
            rule = self._org_wide.best(item_type, name)
        return rule.category if rule else UNCLASSIFIED


def create_rule(actor, organization, item_type, pattern, category, team=None):
    if team is not None and team.organization_id != organization.id:
        raise ValueError("The team belongs to a different organization.")
    with transaction.atomic():
        rule = ClassificationRule.objects.create(
            organization=organization, item_type=item_type, pattern=pattern, category=category, team=team
        )
        record_event(
            actor,
            "classification_rule.created",
            organization=organization,
            target=rule,
            detail=f"{item_type} {pattern} -> {category}"[:300],
        )
    return rule


def change_rule_category(actor, rule, category):
    if rule.category == category:
        return rule
    with transaction.atomic():
        old = rule.category
        rule.category = category
        rule.save(update_fields=["category"])
        record_event(
            actor,
            "classification_rule.changed",
            organization=rule.organization,
            target=rule,
            detail=f"{rule.item_type} {rule.pattern}: {old} -> {category}"[:300],
        )
    return rule


def delete_rule(actor, rule):
    with transaction.atomic():
        detail = f"{rule.item_type} {rule.pattern} ({rule.category})"[:300]
        organization, rule_id = rule.organization, rule.pk
        rule.delete()
        record_event(
            actor,
            "classification_rule.deleted",
            organization=organization,
            detail=f"rule {rule_id}: {detail}"[:300],
        )


def rules_for(viewer):
    """The rules an Admin manages: those of their own organization."""
    if viewer.role != Role.ADMIN:
        return ClassificationRule.objects.none()
    return ClassificationRule.objects.filter(organization=viewer.organization).select_related("team")


def get_rule(viewer, pk):
    return rules_for(viewer).filter(pk=pk).first()
