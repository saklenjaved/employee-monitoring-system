from django import forms

MAX_RANGE_DAYS = 92  # about three months


class DateRangeForm(forms.Form):
    """The start and end date on a dashboard. Both are optional, one date alone means that single day."""

    start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="From")
    end = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="To")

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
