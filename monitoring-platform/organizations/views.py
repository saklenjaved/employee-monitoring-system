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
from accounts.services import create_organization_admin, resend_invite

from .forms import AdminAccountForm, OrganizationForm
from .models import Organization
from .services import create_organization, set_organization_active


@role_required(Role.SUPER_ADMIN)
def organization_list(request):
    organizations = Organization.objects.annotate(
        admin_count=Count("users", filter=Q(users__role=Role.ADMIN))
    )
    return render(request, "organizations/list.html", {"organizations": organizations})


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
    form = AdminAccountForm(request.POST or None)
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
