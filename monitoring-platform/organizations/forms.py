from django import forms

from accounts.models import Role, User

from .models import Organization


class OrganizationForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ["name"]


class AdminAccountForm(forms.Form):
    email = forms.EmailField()
    full_name = forms.CharField(max_length=200)

    def __init__(self, organization, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization

    def clean_email(self):
        # The same email may have an account in another organization; only this organization must not have one.
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email, organization=self.organization).exists():
            raise forms.ValidationError("An account with this email already exists in this organization.")
        if User.objects.filter(email=email, role=Role.SUPER_ADMIN).exists():
            raise forms.ValidationError("This email cannot be used.")
        return email


class AdminEditForm(forms.Form):
    """Edit an Admin's name and email. The current account is not counted as a duplicate."""

    email = forms.EmailField()
    full_name = forms.CharField(max_length=200)

    def __init__(self, admin, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.admin = admin

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email, organization=self.admin.organization).exclude(pk=self.admin.pk).exists():
            raise forms.ValidationError("Another account in this organization already uses this email.")
        if User.objects.filter(email=email, role=Role.SUPER_ADMIN).exists():
            raise forms.ValidationError("This email cannot be used.")
        return email
