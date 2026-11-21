from django import forms

from accounts.models import Role, User
from workforce.models import Department, EmployeeProfile
from workforce.services import visible_employees, visible_teams

from .models import BlurLevel, ScreenshotStatus
from .services import can_view_screenshots

MAX_RANGE_DAYS = 92


class ScreenshotSettingsForm(forms.Form):
    enabled = forms.BooleanField(required=False, label="Capture screenshots")
    interval_seconds = forms.IntegerField(
        required=False, min_value=60, max_value=3600, label="Interval between screenshots (seconds)",
        help_text="Between 60 and 3600. There is no default: choose a value.",
    )
    retention_days = forms.IntegerField(
        required=False, min_value=1, max_value=3650, label="Keep screenshots for (days)",
        help_text="Older screenshots are deleted by the daily cleanup. There is no default: choose a value.",
    )
    blur_level = forms.ChoiceField(
        required=False,
        choices=[("", "Choose...")] + list(BlurLevel.choices),
        label="Blur level",
        help_text="The current Windows agent cannot blur whole screens yet. The level is saved and sent to "
        "the agent, and takes effect after an agent update.",
    )
    managers_can_view = forms.BooleanField(required=False, label="Managers can view screenshots of their teams")
    managers_can_download = forms.BooleanField(required=False, label="Managers can also download screenshots")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("enabled"):
            missing = [
                name
                for name in ("interval_seconds", "retention_days", "blur_level")
                if cleaned.get(name) in (None, "")
            ]
            if missing:
                raise forms.ValidationError(
                    "Choose the interval, the retention period and the blur level before switching screenshots on."
                )
        if cleaned.get("managers_can_download") and not cleaned.get("managers_can_view"):
            raise forms.ValidationError("Managers can only download what they are allowed to view.")
        return cleaned


class ExclusionForm(forms.Form):
    team = forms.ModelChoiceField(queryset=None, required=False, label="Team")
    employee = forms.ModelChoiceField(queryset=None, required=False, label="Employee")

    def __init__(self, organization, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from workforce.models import Team

        # Choices come only from the Admin's own organization.
        self.fields["team"].queryset = Team.objects.filter(organization=organization)
        self.fields["employee"].queryset = User.objects.filter(organization=organization, role=Role.EMPLOYEE)

    def clean(self):
        cleaned = super().clean()
        if bool(cleaned.get("team")) == bool(cleaned.get("employee")):
            raise forms.ValidationError("Choose either a team or an employee.")
        return cleaned


class ScreenshotFilterForm(forms.Form):
    q = forms.CharField(required=False, max_length=120, label="Search")
    employee = forms.ModelChoiceField(queryset=None, required=False)
    team = forms.ModelChoiceField(queryset=None, required=False)
    department = forms.ModelChoiceField(queryset=None, required=False)
    start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="From")
    end = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="To")
    status = forms.ChoiceField(required=False, choices=[("", "Any status")] + list(ScreenshotStatus.choices))
    order = forms.ChoiceField(
        required=False, choices=[("newest", "Newest first"), ("oldest", "Oldest first")], initial="newest"
    )

    def __init__(self, viewer, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only people, teams and departments the viewer may see can be chosen. A viewer who may not look
        # at screenshots gets empty choices, so no names are shown.
        employees = visible_employees(viewer)
        teams = visible_teams(viewer)
        department_ids = EmployeeProfile.objects.filter(user__in=employees).values("department_id")
        departments = Department.objects.filter(organization=viewer.organization, pk__in=department_ids)
        if not can_view_screenshots(viewer):
            employees, teams, departments = employees.none(), teams.none(), departments.none()
        self.fields["employee"].queryset = employees.order_by("full_name")
        self.fields["team"].queryset = teams
        self.fields["department"].queryset = departments
        self.fields["employee"].empty_label = "All"
        self.fields["team"].empty_label = "All"
        self.fields["department"].empty_label = "All"

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        start, end = start or end, end or start
        if start and end:
            if end < start:
                raise forms.ValidationError("The end date is before the start date.")
            if (end - start).days >= MAX_RANGE_DAYS:
                raise forms.ValidationError(f"Choose at most {MAX_RANGE_DAYS} days.")
        cleaned["start"], cleaned["end"] = start, end
        return cleaned
