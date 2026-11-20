from django import forms
from django.core.validators import RegexValidator

pin_validator = RegexValidator(r"^\d{6}$", "The PIN must be exactly 6 digits.")


class MonitoringPinForm(forms.Form):
    """Set or change the monitoring PIN. The current password is required, so a left-open session cannot change it."""

    current_password = forms.CharField(widget=forms.PasswordInput, strip=False)
    pin = forms.CharField(max_length=6, validators=[pin_validator], widget=forms.PasswordInput)
    pin_confirm = forms.CharField(max_length=6, widget=forms.PasswordInput)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean(self):
        cleaned = super().clean()
        if "current_password" in cleaned and not self.user.check_password(cleaned["current_password"]):
            self.add_error("current_password", "The password is not correct.")
        if cleaned.get("pin") and cleaned.get("pin") != cleaned.get("pin_confirm"):
            self.add_error("pin_confirm", "The two PINs do not match.")
        return cleaned


class PairingApproveForm(forms.Form):
    """Approving a PC needs the account password, so a left-open browser cannot approve a foreign PC."""

    current_password = forms.CharField(widget=forms.PasswordInput, strip=False)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_current_password(self):
        password = self.cleaned_data["current_password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("The password is not correct.")
        return password
