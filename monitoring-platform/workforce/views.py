"""Admin pages for departments, teams and people (Employees and Managers).

Only an Admin may use them, and every lookup is limited to the Admin's own
organization, so another organization's record is simply "not found".
"""
from urllib.parse import urlencode

from django.contrib import messages
from django.db import IntegrityError
from django.http import Http404
from django.urls import reverse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from access_control.permissions import role_required
from accounts.models import Role
from accounts.services import resend_invite
from common.listing import Column, Filter, build_listing
from devices.models import Device
from devices.services import next_setup_step, setup_steps
from devices.start import monitoring_status

from . import services
from .forms import DepartmentForm, PersonCreateForm, PersonEditForm, TeamForm
from .models import EmployeeProfile

# Shown if two Admins save the same name at the same moment, after the form checks passed.
CONFLICT_MESSAGE = "Could not save because a value is already in use. Please check the form and try again."

DEPARTMENT_COLUMNS = (Column("Name", "name", "name", True),)
TEAM_COLUMNS = (
    Column("Name", "name", "name", True),
    Column("Department", "department", "department__name", True),
    Column(""),
)
PERSON_COLUMNS = (
    Column("Name", "name", "full_name", True),
    Column("Email", "email", "email", True),
    Column("Role", "role", "role", True),
    Column("Department", "department", "department_name", True),
    Column("Status", "status", "is_active"),
)
ROLE_CHOICES = [(Role.EMPLOYEE.value, "Employee"), (Role.MANAGER.value, "Manager")]
STATUS_CHOICES = [("active", "Active"), ("pending", "Pending activation"), ("deactivated", "Deactivated")]


def _filter_status(rows, value):
    # A pending person is inactive too, so the status filter checks the invitation first.
    if value == "pending":
        return rows.filter(has_pending_invite=True)
    return rows.filter(has_pending_invite=False, is_active=(value == "active"))


def _department_choices(viewer):
    return [(str(department.pk), department.name) for department in services.departments_for(viewer)]


@role_required(Role.ADMIN)
def department_list(request):
    organization = request.user.organization
    form = DepartmentForm(organization, request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.create_department(request.user, organization, form.cleaned_data["name"])
        except IntegrityError:
            form.add_error(None, CONFLICT_MESSAGE)
        else:
            messages.success(request, "Department created.")
            return redirect("department_list")
    listing = build_listing(
        request,
        services.departments_for(request.user),
        DEPARTMENT_COLUMNS,
        search_fields=("name",),
        default_sort="name",
    )
    return render(request, "workforce/department_list.html", {"listing": listing, "form": form})


@role_required(Role.ADMIN)
def team_list(request):
    listing = build_listing(
        request,
        services.teams_for(request.user),
        TEAM_COLUMNS,
        search_fields=("name", "department__name"),
        filters=(
            Filter(
                "department",
                "Department",
                _department_choices(request.user),
                lambda rows, value: rows.filter(department_id=value),
            ),
        ),
        default_sort="name",
    )
    return render(request, "workforce/team_list.html", {"listing": listing})


@role_required(Role.ADMIN)
def team_create(request):
    organization = request.user.organization
    form = TeamForm(organization, request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            team = services.create_team(
                request.user, organization, data["name"], data["department"], data["managers"], data["employees"]
            )
        except IntegrityError:
            form.add_error(None, CONFLICT_MESSAGE)
        except ValueError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, f"Team '{team.name}' created.")
            return redirect("team_list")
    return render(request, "workforce/team_form.html", {"form": form, "team": None})


@role_required(Role.ADMIN)
def team_edit(request, pk):
    team = services.get_team(request.user, pk)
    if team is None:
        raise Http404
    organization = request.user.organization
    initial = {
        "name": team.name,
        "department": team.department,
        "managers": [m.user_id for m in team.memberships.filter(user__role=Role.MANAGER)],
        "employees": [m.user_id for m in team.memberships.filter(user__role=Role.EMPLOYEE)],
    }
    form = TeamForm(organization, request.POST or None, team=team, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            services.update_team(request.user, team, data["name"], data["department"], data["managers"], data["employees"])
        except IntegrityError:
            form.add_error(None, CONFLICT_MESSAGE)
        except ValueError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Team saved.")
            return redirect("team_list")
    return render(request, "workforce/team_form.html", {"form": form, "team": team})


@role_required(Role.ADMIN)
def person_list(request):
    listing = build_listing(
        request,
        services.people_for(request.user),
        PERSON_COLUMNS,
        search_fields=("full_name", "email", "employee_code"),
        filters=(
            Filter("role", "Role", ROLE_CHOICES, lambda rows, value: rows.filter(role=value)),
            Filter(
                "status",
                "Status",
                STATUS_CHOICES,
                _filter_status,
            ),
            Filter(
                "department",
                "Department",
                _department_choices(request.user),
                lambda rows, value: rows.filter(department_id=value),
            ),
        ),
        default_sort="name",
    )
    return render(request, "workforce/person_list.html", {"listing": listing})


@role_required(Role.ADMIN)
def person_create(request):
    organization = request.user.organization
    form = PersonCreateForm(organization, request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            user, email_sent = services.create_person(
                request.user,
                organization,
                data["email"],
                data["full_name"],
                data["role"],
                data["employee_code"],
                data["department"],
                request,
            )
        except IntegrityError:
            form.add_error(None, CONFLICT_MESSAGE)
        else:
            if email_sent:
                messages.success(request, f"Invitation created. An activation code was emailed to {user.email}.")
            else:
                messages.warning(request, f"Invitation for {user.email} was created, but the email could not be sent. Send a new code.")
            return redirect("person_detail", pk=user.pk)
    return render(request, "workforce/person_create.html", {"form": form})


@role_required(Role.ADMIN)
def person_detail(request, pk):
    person = services.get_person(request.user, pk)
    if person is None:
        raise Http404
    organization = request.user.organization
    profile = EmployeeProfile.objects.filter(user=person, organization=organization).first()
    initial = {
        "full_name": person.full_name,
        "employee_code": profile.employee_code if profile else "",
        "department": profile.department if profile else None,
    }
    form = PersonEditForm(organization, person, request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            services.update_person(request.user, person, data["full_name"], data["employee_code"], data["department"])
        except IntegrityError:
            form.add_error(None, CONFLICT_MESSAGE)
        else:
            messages.success(request, "Saved.")
            return redirect("person_detail", pk=person.pk)
    devices = Device.objects.filter(organization=organization, user=person)
    teams = person.team_memberships.filter(organization=organization).select_related("team")
    return render(
        request,
        "workforce/person_detail.html",
        {"person": person, "form": form, "devices": devices, "teams": teams, **_setup_context(person),
         "monitoring_back": _monitoring_back(request, person),
         "nav_current": "dashboard_employee" if _monitoring_back(request, person) else ""},
    )


def _monitoring_back(request, person):
    """When the profile was opened from an employee's monitoring page (?from=monitoring), the link back to it."""
    if request.GET.get("from") != "monitoring" or person.role != Role.EMPLOYEE:
        return ""
    dates = urlencode({key: request.GET[key] for key in ("start", "end") if request.GET.get(key)})
    return reverse("dashboard_employee", args=[person.pk]) + (f"?{dates}" if dates else "")


def _setup_context(person):
    steps = setup_steps(person) if person.role == Role.EMPLOYEE else []
    has_pc = Device.objects.filter(user=person, is_active=True).exists()
    # The real state: "Monitoring active" only after the agent confirmed a start and reports it is collecting.
    status = monitoring_status(person) if person.role == Role.EMPLOYEE else None
    return {"setup": steps, "next_setup": next_setup_step(steps), "has_pc": has_pc, "monitoring": status}


@role_required(Role.ADMIN)
def person_setup_status(request, pk):
    """The Monitoring setup card on the person page. The page refreshes it every few seconds."""
    person = services.get_person(request.user, pk)
    if person is None:
        raise Http404
    return render(request, "workforce/_setup_card.html", {"person": person, **_setup_context(person)})


@role_required(Role.ADMIN)
@require_POST
def person_set_active(request, pk):
    person = services.get_person(request.user, pk)
    if person is None:
        raise Http404
    active = request.POST.get("active") == "1"
    try:
        services.set_person_active(request.user, person, active)
    except ValueError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"{person.email} {'activated' if active else 'deactivated'}.")
    return redirect("person_detail", pk=person.pk)


@role_required(Role.ADMIN)
@require_POST
def person_resend_invite(request, pk):
    person = services.get_person(request.user, pk)
    if person is None:
        raise Http404
    try:
        sent = resend_invite(request.user, person, request)
    except ValueError as error:
        messages.error(request, str(error))
    else:
        if sent:
            messages.success(request, f"A new activation code was emailed to {person.email}. The older code no longer works.")
        else:
            messages.warning(request, f"The activation code for {person.email} could not be sent. Try again later.")
    return redirect("person_detail", pk=person.pk)
