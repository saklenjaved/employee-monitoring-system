from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from .models import MembershipStatus, Role, User


class ProfileForm(forms.ModelForm):
    """The part of the profile a person may change alone. Email, role and organization are set by an Admin."""

    class Meta:
        model = User
        fields = ["full_name"]
        labels = {"full_name": "Full name"}


class ActivationForm(forms.Form):
    """What an invited person types on the activation page. The password is only for a new account."""

    email = forms.EmailField(label="Email")
    code = forms.CharField(
        label="Activation code",
        min_length=8,
        max_length=8,
        help_text="The 8 digits from your invitation email.",
    )
    password = forms.CharField(
        label="Choose a password",
        required=False,
        widget=forms.PasswordInput,
        help_text="Only if you are new to Employee Monitoring. Leave blank if you already have a password.",
    )
    password_confirm = forms.CharField(label="Confirm password", required=False, widget=forms.PasswordInput)

    def clean_code(self):
        code = self.cleaned_data["code"].strip()
        if not code.isdigit():
            raise ValidationError("The activation code is 8 digits.", code="invalid_code")
        return code

    def clean(self):
        data = super().clean()
        if data.get("password") != data.get("password_confirm"):
            raise ValidationError("The two passwords do not match.", code="password_mismatch")
        return data


class OrganizationLoginForm(AuthenticationForm):
    """Login form that also refuses users whose organization is deactivated."""

    def clean_username(self):
        # Emails are stored in lower case, so "Admin@Org.test" must find "admin@org.test".
        return self.cleaned_data["username"].strip().lower()

    def get_invalid_login_error(self):
        # Django rejects inactive accounts before confirm_login_allowed runs, so a person who has not
        # activated yet is told so here, but only after the right password was typed. A wrong password
        # keeps the generic message.
        username = self.cleaned_data.get("username", "")
        user = User.objects.filter(email=username, is_active=False).first()
        if user and user.check_password(self.cleaned_data.get("password", "")):
            if user.memberships.filter(status=MembershipStatus.PENDING).exists():
                return ValidationError(
                    "Your account is not active yet. Activate it with the code from your invitation email.",
                    code="not_activated",
                )
            if user.memberships.filter(status=MembershipStatus.ENDED).exists():
                return ValidationError(
                    "Your access to this organization has ended. Contact your administrator.",
                    code="access_ended",
                )
            return ValidationError(
                "Your account has been deactivated by your administrator. Contact your administrator to reactivate it.",
                code="deactivated",
            )
        return super().get_invalid_login_error()

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.organization_id and not user.organization.is_active:
            raise ValidationError(
                "Your organization is deactivated. Please contact your administrator.",
                code="organization_inactive",
            )
        if user.role != Role.SUPER_ADMIN and not user.memberships.filter(status=MembershipStatus.ACTIVE).exists():
            # Same message as a wrong password, so this does not reveal which accounts exist.
            raise ValidationError(self.error_messages["invalid_login"], code="invalid_login")
