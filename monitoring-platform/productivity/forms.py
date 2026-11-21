from django import forms

from monitoring.validation import DOMAIN, file_name
from workforce.models import Team

from .models import Category, ClassificationRule, ItemType


class RuleForm(forms.Form):
    item_type = forms.ChoiceField(choices=ItemType.choices)
    pattern = forms.CharField(
        max_length=260,
        label="Name",
        help_text="An application's file name such as chrome.exe, or a website domain such as example.com.",
    )
    category = forms.ChoiceField(choices=Category.choices)
    team = forms.ModelChoiceField(
        queryset=Team.objects.none(), required=False, empty_label="Whole organization", label="Applies to"
    )

    def __init__(self, organization, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        # Teams of the Admin's own organization only.
        self.fields["team"].queryset = Team.objects.filter(organization=organization)

    def clean(self):
        cleaned = super().clean()
        item_type, pattern = cleaned.get("item_type"), cleaned.get("pattern")
        if not item_type or pattern is None:
            return cleaned
        pattern = pattern.strip().lower()
        if item_type == ItemType.WEBSITE:
            pattern = pattern.removeprefix("www.")
            if len(pattern) > 253 or not DOMAIN.fullmatch(pattern):
                self.add_error("pattern", "Enter a domain such as example.com.")
                return cleaned
        elif file_name(pattern) is None:
            self.add_error("pattern", "Enter the application's file name only, such as chrome.exe.")
            return cleaned
        cleaned["pattern"] = pattern
        duplicate = ClassificationRule.objects.filter(
            organization=self.organization, item_type=item_type, pattern=pattern, team=cleaned.get("team")
        )
        if duplicate.exists():
            self.add_error("pattern", "A rule for this item already exists in this scope.")
        return cleaned
