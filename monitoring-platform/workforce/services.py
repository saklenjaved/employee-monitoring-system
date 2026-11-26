"""Departments, teams, employees and managers.

Every function takes the logged-in user (`viewer` or `actor`). The organization
always comes from that user on the server, never from request data.
"""
from django.db import transaction
from django.db.models import Exists, OuterRef, Subquery

from accounts.models import Membership, MembershipStatus, Role, User
from accounts.services import (
    OPEN_STATUSES,
    open_invitation,
    open_membership_in,
    pending_membership,
    send_activation_code,
    set_user_active,
)
from audit.services import record_event

from .models import Department, EmployeeProfile, Team, UserTeam

PERSON_ROLES = (Role.EMPLOYEE, Role.MANAGER)


# --- What a user may see ----------------------------------------------------------------


def departments_for(viewer):
    if viewer.role != Role.ADMIN:
        return Department.objects.none()
    return Department.objects.filter(organization=viewer.organization)


def teams_for(viewer):
    if viewer.role != Role.ADMIN:
        return Team.objects.none()
    return Team.objects.filter(organization=viewer.organization).select_related("department")


def people_for(viewer):
    """Employees and Managers that an Admin manages. Other roles get nothing."""
    if viewer.role != Role.ADMIN:
        return User.objects.none()
    organization = viewer.organization
    # Memberships and employee records are looked up in this organization only, so a person's
    # history in another organization never shows here.
    memberships = Membership.objects.filter(user=OuterRef("pk"), organization=organization)
    record = EmployeeProfile.objects.filter(user=OuterRef("pk"), organization=organization)
    return User.objects.filter(organization=organization, role__in=PERSON_ROLES).annotate(
        is_member=Exists(memberships.filter(status__in=OPEN_STATUSES)),
        has_pending_invite=Exists(memberships.filter(status=MembershipStatus.PENDING).exclude(role="")),
        awaiting_role=Exists(memberships.filter(status=MembershipStatus.PENDING, role="")),
        employee_code=Subquery(record.values("employee_code")[:1]),
        department_id=Subquery(record.values("department_id")[:1]),
        department_name=Subquery(record.values("department__name")[:1]),
    )


def get_team(viewer, pk):
    return teams_for(viewer).filter(pk=pk).first()


def visible_teams(viewer):
    """Teams whose data `viewer` may see: all teams of the organization for an Admin,
    the teams they belong to for a Manager, none for anyone else."""
    teams = Team.objects.select_related("department")
    if viewer.role == Role.ADMIN:
        return teams.filter(organization=viewer.organization)
    if viewer.role == Role.MANAGER:
        member_of = UserTeam.objects.filter(user=viewer, organization=viewer.organization).values("team_id")
        return teams.filter(organization=viewer.organization, pk__in=member_of)
    return teams.none()


def get_person(viewer, pk):
    return people_for(viewer).filter(pk=pk).first()


def visible_employees(viewer):
    """Employees whose monitoring data `viewer` may see.

    Admin: the whole organization. Manager: employees in the teams they manage.
    Employee: only themselves. Super Admin: nobody (default, to confirm).
    """
    employees = User.objects.filter(role=Role.EMPLOYEE)
    if viewer.role == Role.ADMIN:
        return employees.filter(organization=viewer.organization)
    if viewer.role == Role.MANAGER:
        team_ids = UserTeam.objects.filter(user=viewer, organization=viewer.organization).values("team_id")
        return employees.filter(
            organization=viewer.organization, team_memberships__team_id__in=team_ids
        ).distinct()
    if viewer.role == Role.EMPLOYEE:
        return employees.filter(pk=viewer.pk)
    return employees.none()


# --- Changes ----------------------------------------------------------------------------


def _same_organization(organization, *objects):
    for obj in objects:
        if obj is not None and obj.organization_id != organization.id:
            raise ValueError("Object belongs to a different organization.")


def _set_members(team, managers, employees):
    """Make the team's members exactly these users. Returns (added_ids, removed_ids).

    Call it inside a transaction. A ValueError means nothing was changed.
    """
    managers, employees = list(managers), list(employees)
    for user in managers:
        if user.organization_id != team.organization_id or user.role != Role.MANAGER:
            raise ValueError("Managers must be Manager accounts of this organization.")
    for user in employees:
        if user.organization_id != team.organization_id or user.role != Role.EMPLOYEE:
            raise ValueError("Employees must be Employee accounts of this organization.")

    wanted = {user.pk for user in managers + employees}
    current = set(team.memberships.values_list("user_id", flat=True))
    removed, added = current - wanted, wanted - current
    team.memberships.filter(user_id__in=removed).delete()
    for user_id in added:
        UserTeam.objects.create(organization=team.organization, team=team, user_id=user_id)
    return sorted(added), sorted(removed)


def _member_note(added, removed):
    """A short audit note with user IDs only. Who sees which employees depends on team membership."""
    return f"added: {','.join(map(str, added)) or '-'}; removed: {','.join(map(str, removed)) or '-'}"[:300]


def create_department(actor, organization, name):
    with transaction.atomic():
        department = Department.objects.create(organization=organization, name=name)
        record_event(actor, "department.created", organization=organization, target=department)
    return department


def create_team(actor, organization, name, department, managers=(), employees=()):
    """Create a team with its managers and employees in one step."""
    _same_organization(organization, department)
    with transaction.atomic():
        team = Team.objects.create(organization=organization, name=name, department=department)
        added, removed = _set_members(team, managers, employees)
        record_event(
            actor, "team.created", organization=organization, target=team, detail=_member_note(added, removed)
        )
    return team


def update_team(actor, team, name, department, managers, employees):
    """Rename a team and set its department, managers and employees."""
    organization = team.organization
    _same_organization(organization, department)
    with transaction.atomic():
        team.name = name
        team.department = department
        team.save(update_fields=["name", "department"])
        added, removed = _set_members(team, managers, employees)
        record_event(
            actor, "team.updated", organization=organization, target=team, detail=_member_note(added, removed)
        )
    return team


def create_person(actor, organization, email, full_name, role, employee_code, department, request):
    """Invite an Employee or Manager and email them an activation code. Returns (user, email_sent).

    A person who worked here before gets their old employee record back with the new details.
    Raises ValueError when the invitation is refused (see accounts.services.open_invitation).
    """
    if role not in PERSON_ROLES:
        raise ValueError("Only Employee and Manager accounts are created here.")
    _same_organization(organization, department)
    with transaction.atomic():
        user, membership, code = open_invitation(actor, organization, email, full_name, role)
        EmployeeProfile.objects.update_or_create(
            user=user,
            organization=organization,
            defaults={"department": department, "employee_code": employee_code},
        )
    # Sent after the commit, so a rolled-back invitation never emails a code.
    return user, send_activation_code(request, membership, code)


def update_person(actor, person, full_name, employee_code, department):
    organization = person.organization
    _same_organization(organization, department)
    with transaction.atomic():
        person.full_name = full_name
        person.save(update_fields=["full_name"])
        profile, _ = EmployeeProfile.objects.get_or_create(user=person, organization=organization)
        profile.employee_code = employee_code
        profile.department = department
        profile.save(update_fields=["employee_code", "department"])
        record_event(actor, "employee.updated", organization=organization, target=person)


def set_person_active(actor, person, active):
    """Activate or deactivate an Employee or Manager. An Admin cannot deactivate themselves."""
    if person.pk == actor.pk:
        raise ValueError("You cannot change your own status.")
    if open_membership_in(person, actor.organization) is None:
        raise ValueError("This person was removed from the organization. Invite them again to give access back.")
    if pending_membership(person) is not None:
        # Only the activation code opens a pending account. Send a new code instead.
        raise ValueError("This person has not activated the account yet.")
    from devices.services import revoke_devices_for  # local import: devices and workforce import each other

    set_user_active(actor, person, active)
    # Deactivating or reactivating resets the whole setup, so the employee downloads and installs WinAgent again.
    revoke_devices_for(actor, actor.organization, person)
