from django import forms

from accounts.models import User

from .models import Organization


class OrganizationForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ["name"]


class AdminAccountForm(forms.Form):
    email = forms.EmailField()
    full_name = forms.CharField(max_length=200)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email
