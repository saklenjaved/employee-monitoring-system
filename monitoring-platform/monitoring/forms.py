from zoneinfo import available_timezones

from django import forms

DAY_CHOICES = [
    (0, "Monday"),
    (1, "Tuesday"),
    (2, "Wednesday"),
    (3, "Thursday"),
    (4, "Friday"),
    (5, "Saturday"),
    (6, "Sunday"),
]


class MonitoringPolicyForm(forms.Form):
    enabled = forms.BooleanField(required=False, label="Monitoring enabled")
    days = forms.TypedMultipleChoiceField(
        choices=DAY_CHOICES, coerce=int, widget=forms.CheckboxSelectMultiple, label="Monitoring days"
    )
    start_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}), label="Start time")
    end_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}), label="End time")
    sample_interval_seconds = forms.IntegerField(min_value=5, max_value=300, label="Sample interval (seconds)")
    idle_threshold_seconds = forms.IntegerField(min_value=60, max_value=3600, label="Idle threshold (seconds)")
    sync_interval_seconds = forms.IntegerField(min_value=30, max_value=900, label="Upload interval (seconds)")
    timezone = forms.CharField(
        max_length=64,
        label="Time zone",
        help_text='Daily totals are counted per calendar day in this zone, for example "Asia/Kolkata" or "UTC".',
    )

    def clean_timezone(self):
        name = self.cleaned_data["timezone"].strip()
        if name not in available_timezones():
            raise forms.ValidationError("Unknown time zone. Use a name such as Europe/London or UTC.")
        return name

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_time"), cleaned.get("end_time")
        if start and end and start == end:
            raise forms.ValidationError("Start time and end time must be different.")
        return cleaned
