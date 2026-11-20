from django import forms
from django.db.models import Q

from accounts.models import Role, User

from .models import Department, EmployeeProfile, Team


class DepartmentForm(forms.Form):
    name = forms.CharField(max_length=100)

    def __init__(self, organization, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if Department.objects.filter(organization=self.organization, name__iexact=name).exists():
            raise forms.ValidationError("A department with this name already exists.")
        return name


class TeamForm(forms.Form):
    name = forms.CharField(max_length=100)
    department = forms.ModelChoiceField(queryset=Department.objects.none(), required=False)
    managers = forms.ModelMultipleChoiceField(queryset=User.objects.none(), required=False)
    employees = forms.ModelMultipleChoiceField(queryset=User.objects.none(), required=False)

    def __init__(self, organization, *args, team=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.team = team
        # Choices come only from the Admin's own organization.
        self.fields["department"].queryset = Department.objects.filter(organization=organization)
        # Active people can be added. Inactive people who are already in this team stay
        # selectable, so saving the form does not silently remove them.
        allowed = Q(is_active=True)
        if team is not None:
            allowed |= Q(team_memberships__team=team)
        self.fields["managers"].queryset = User.objects.filter(
            allowed, organization=organization, role=Role.MANAGER
        ).distinct()
        self.fields["employees"].queryset = User.objects.filter(
            allowed, organization=organization, role=Role.EMPLOYEE
        ).distinct()

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        others = Team.objects.filter(organization=self.organization, name__iexact=name)
        if self.team is not None:
            others = others.exclude(pk=self.team.pk)
        if others.exists():
            raise forms.ValidationError("A team with this name already exists.")
        return name


class PersonCreateForm(forms.Form):
    email = forms.EmailField()
    full_name = forms.CharField(max_length=200)
    role = forms.ChoiceField(choices=[(Role.EMPLOYEE.value, "Employee"), (Role.MANAGER.value, "Manager")])
    employee_code = forms.CharField(max_length=50, required=False)
    department = forms.ModelChoiceField(queryset=Department.objects.none(), required=False)

    def __init__(self, organization, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.fields["department"].queryset = Department.objects.filter(organization=organization)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            # Neutral wording: it must not reveal that the email belongs to another organization.
            raise forms.ValidationError("This email cannot be used.")
        return email

    def clean_employee_code(self):
        code = self.cleaned_data["employee_code"].strip()
        if code and EmployeeProfile.objects.filter(organization=self.organization, employee_code=code).exists():
            raise forms.ValidationError("This employee code is already used.")
        return code


class PersonEditForm(forms.Form):
    full_name = forms.CharField(max_length=200)
    employee_code = forms.CharField(max_length=50, required=False)
    department = forms.ModelChoiceField(queryset=Department.objects.none(), required=False)

    def __init__(self, organization, person, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.person = person
        self.fields["department"].queryset = Department.objects.filter(organization=organization)

    def clean_employee_code(self):
        code = self.cleaned_data["employee_code"].strip()
        others = EmployeeProfile.objects.filter(organization=self.organization, employee_code=code)
        if code and others.exclude(user=self.person).exists():
            raise forms.ValidationError("This employee code is already used.")
        return code
