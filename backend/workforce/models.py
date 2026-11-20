from django.conf import settings
from django.db import models
from django.db.models import Q


class Department(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT, related_name="departments"
    )
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="unique_department_name_per_org"),
        ]

    def __str__(self):
        return self.name


class Team(models.Model):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="teams")
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="teams")
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="unique_team_name_per_org"),
        ]

    def __str__(self):
        return self.name


class UserTeam(models.Model):
    """Puts a user in a team. A user with the Manager role is that team's manager;
    a user with the Employee role is a team member."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_memberships")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["team", "user"], name="unique_user_per_team"),
        ]


class EmployeeProfile(models.Model):
    """Work details for an Employee or Manager in one organization: employee code and department.

    A person has one record per organization they have worked in. After a transfer the old
    organization keeps its record as history and the new organization gets its own.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="employee_profiles")
    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    employee_code = models.CharField(max_length=50, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="one_profile_per_user_per_org"),
            # An employee code, when given, is unique inside the organization.
            models.UniqueConstraint(
                fields=["organization", "employee_code"],
                condition=~Q(employee_code=""),
                name="unique_employee_code_per_org",
            ),
        ]
