"""Organization management. Only the Super Admin may use these pages (PRD US-10).

The Super Admin works across all organizations by design, so these views do
not filter by a single organization. Every other role gets 403 Forbidden.
"""
from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from access_control.permissions import role_required
from accounts.models import MembershipStatus, Role, User
from accounts.services import create_organization_admin, platform_admins, resend_invite
from common.listing import Column, Filter, build_listing

from .forms import AdminAccountForm, AdminEditForm, OrganizationForm
from .models import Organization
from .services import (
    create_organization,
    delete_organization_admin,
    set_organization_active,
    set_organization_admin_active,
    update_organization_admin,
)


ADMIN_COLUMNS = (
    Column("Name", "name", "full_name", True),
    Column("Email", "email", "email", True),
    Column("Organization", "organization", "organization__name", True),
    Column("Status", "status", "admin_status"),
    Column("Created", "created", "date_joined"),
    Column(""),
)
ADMIN_STATUS_CHOICES = [("active", "Active"), ("pending", "Pending"), ("inactive", "Inactive")]


@role_required(Role.SUPER_ADMIN)
def admin_list(request):
    """Every Admin account across all organizations, with search and filters."""
    organization_choices = [(str(org.pk), org.name) for org in Organization.objects.order_by("name")]
    listing = build_listing(
        request,
        platform_admins(request.user),
        ADMIN_COLUMNS,
        search_fields=("full_name", "email", "organization__name"),
        filters=(
            Filter("status", "Status", ADMIN_STATUS_CHOICES, lambda rows, value: rows.filter(admin_status=value)),
            Filter(
                "organization", "Organization", organization_choices, lambda rows, value: rows.filter(organization_id=value)
            ),
        ),
        default_sort="created",
        default_descending=True,
    )
    return render(request, "organizations/admin_list.html", {"listing": listing})


@role_required(Role.SUPER_ADMIN)
def organization_list(request):
    organizations = Organization.objects.annotate(
        admin_count=Count("users", filter=Q(users__role=Role.ADMIN))
    ).order_by("name")
    return render(request, "organizations/list.html", {"organizations": organizations})


@role_required(Role.SUPER_ADMIN)
def organization_detail(request, pk):
    """One organization and its Admin accounts."""
    organization = get_object_or_404(Organization, pk=pk)
    admins = platform_admins(request.user).filter(organization=organization).order_by("full_name", "pk")
    return render(request, "organizations/detail.html", {"organization": organization, "admins": admins})


@role_required(Role.SUPER_ADMIN)
def organization_create(request):
    form = OrganizationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        organization = create_organization(request.user, form.cleaned_data["name"])
        messages.success(request, f"Organization '{organization.name}' created.")
        return redirect("organization_list")
    return render(request, "organizations/create.html", {"form": form})


@role_required(Role.SUPER_ADMIN)
@require_POST
def organization_set_active(request, pk):
    organization = get_object_or_404(Organization, pk=pk)
    active = request.POST.get("active") == "1"
    set_organization_active(request.user, organization, active)
    state = "activated" if active else "deactivated"
    messages.success(request, f"Organization '{organization.name}' {state}.")
    return redirect("organization_list")


@role_required(Role.SUPER_ADMIN)
def organization_admin_create(request, pk):
    organization = get_object_or_404(Organization, pk=pk)
    form = AdminAccountForm(organization, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user, email_sent = create_organization_admin(
            request.user,
            organization,
            form.cleaned_data["email"],
            form.cleaned_data["full_name"],
            request,
        )
        if email_sent:
            messages.success(request, f"Admin invitation created. An activation code was emailed to {user.email}.")
        else:
            messages.warning(
                request,
                f"Admin invitation for {user.email} was created, but the email could not be sent. Send a new code.",
            )
        return redirect("organization_list")
    pending_admins = User.objects.filter(
        memberships__organization=organization,
        memberships__role=Role.ADMIN,
        memberships__status=MembershipStatus.PENDING,
    ).order_by("email")
    return render(
        request,
        "organizations/admin_create.html",
        {"form": form, "organization": organization, "pending_admins": pending_admins},
    )


@role_required(Role.SUPER_ADMIN)
@require_POST
def organization_admin_resend_invite(request, pk):
    user = get_object_or_404(User, pk=pk, role=Role.ADMIN)
    try:
        sent = resend_invite(request.user, user, request)
    except ValueError as error:
        messages.error(request, str(error))
    else:
        if sent:
            messages.success(request, f"A new activation code was emailed to {user.email}. The older code no longer works.")
        else:
            messages.warning(request, f"The activation code for {user.email} could not be sent. Try again later.")
    return redirect("organization_admin_create", pk=user.organization_id)


@role_required(Role.SUPER_ADMIN)
@require_POST
def organization_admin_set_active(request, pk):
    user = get_object_or_404(User, pk=pk, role=Role.ADMIN)
    active = request.POST.get("active") == "1"
    try:
        set_organization_admin_active(request.user, user, active)
    except ValueError as error:
        messages.error(request, str(error))
    else:
        state = "activated" if active else "deactivated"
        messages.success(request, f"Admin {user.email} {state}.")
    if request.POST.get("next") == "organization" and user.organization_id:
        return redirect("organization_detail", pk=user.organization_id)
    return redirect("admin_list")


@role_required(Role.SUPER_ADMIN)
def organization_admin_edit(request, pk):
    """Super Admin: an Admin's name and email, pre-filled from the account."""
    admin = get_object_or_404(platform_admins(request.user), pk=pk)
    form = AdminEditForm(admin, request.POST or None, initial={"email": admin.email, "full_name": admin.full_name})
    if request.method == "POST" and form.is_valid():
        try:
            update_organization_admin(request.user, admin, form.cleaned_data["email"], form.cleaned_data["full_name"])
        except ValueError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, f"Admin {admin.email} was updated.")
            return redirect("admin_list")
    return render(request, "organizations/admin_edit.html", {"admin": admin, "form": form})


@role_required(Role.SUPER_ADMIN)
@require_POST
def organization_admin_delete(request, pk):
    """Super Admin: permanently delete an Admin's account. The organization and its employees are kept."""
    admin = get_object_or_404(platform_admins(request.user), pk=pk)
    email = admin.email
    try:
        delete_organization_admin(request.user, admin)
    except ValueError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Admin {email} was deleted. The organization and its employees were kept.")
    return redirect("admin_list")
