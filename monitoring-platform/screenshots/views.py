"""Screenshot pages. Settings and exclusions are for Admins. The list and the images are for
Admins and Managers, limited by screenshots.services.visible_screenshots. Every look at an image
and every download is written to the audit log by the service.
"""
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import OuterRef, Q, Subquery
from django.http import Http404, HttpResponseRedirect, QueryDict
from django.urls import reverse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from access_control.permissions import role_required
from accounts.models import Role
from devices.models import Device
from devices.services import agent_policy
from monitoring.services import zone_context
from workforce.models import EmployeeProfile, UserTeam
from workforce.services import visible_employees

from . import services
from .forms import ExclusionForm, ScreenshotFilterForm, ScreenshotSettingsForm
from .models import Screenshot, ScreenshotExclusion, ScreenshotStatus

PAGE_SIZE = 24
DISPLAY_ZONE = ZoneInfo(settings.DISPLAY_TIMEZONE)


def _device_rows(organization):
    """One row per active PC: whether its agent has the current screenshot settings, and the latest image.

    "Applied" means the PC fetched the policy version the server expects (recorded when it was served).
    """
    rows = []
    devices = Device.objects.filter(organization=organization, is_active=True).select_related("user")
    for device in devices.order_by("user__full_name"):
        policy = agent_policy(device)
        shots = policy["screenshots"]
        applied = device.policy_version_served == policy["policy_version"]
        if not shots["enabled"]:
            state = "Monitoring disabled"
        elif not device.is_online:
            state = "Waiting for agent connection"
        elif not applied:
            state = "Waiting for agent to apply settings"
        else:
            state = "Screenshot monitoring active"
        last = Screenshot.objects.filter(device=device).order_by("-captured_at").first()
        rows.append({
            "name": device.user.full_name,
            "state": state,
            "applied": applied,
            "online": device.is_online,
            "interval": shots.get("interval_seconds"),
            "last": last,
            "last_when": last.captured_at.astimezone(DISPLAY_ZONE) if last else None,
        })
    return rows


@role_required(Role.ADMIN)
def settings_page(request):
    organization = request.user.organization
    policy = services.get_policy(organization)
    initial = {name: getattr(policy, name) for name in services.POLICY_FIELDS}
    form = ScreenshotSettingsForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        values = {name: form.cleaned_data[name] for name in services.POLICY_FIELDS}
        values["interval_seconds"] = values["interval_seconds"] or None
        values["retention_days"] = values["retention_days"] or None
        try:
            services.update_policy(request.user, organization, values)
        except ValueError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Settings saved. Each PC applies them within about a minute; the table below shows which PCs have them.")
            return redirect("screenshot_settings")
    return render(request, "screenshots/settings.html", {
        "form": form,
        "policy": policy,
        "device_rows": _device_rows(organization),
    })


@role_required(Role.ADMIN)
def exclusions_page(request):
    organization = request.user.organization
    form = ExclusionForm(organization, request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.add_exclusion(
                request.user, organization, team=form.cleaned_data["team"], user=form.cleaned_data["employee"]
            )
        except IntegrityError:
            form.add_error(None, "That team or employee is already excluded.")
        else:
            messages.success(request, "Exclusion added.")
            return redirect("screenshot_exclusions")
    exclusions = ScreenshotExclusion.objects.filter(organization=organization).select_related("team", "user")
    return render(request, "screenshots/exclusions.html", {"form": form, "exclusions": exclusions})


@role_required(Role.ADMIN)
@require_POST
def exclusion_remove(request, pk):
    exclusion = ScreenshotExclusion.objects.filter(pk=pk, organization=request.user.organization).first()
    if exclusion is None:
        raise Http404
    services.remove_exclusion(request.user, exclusion)
    messages.success(request, "Exclusion removed.")
    return redirect("screenshot_exclusions")


def _context_employee(request):
    """The employee this list was opened from (?employee=ID), or None.

    Only an employee the viewer may see counts. Anything else falls back to the normal list, so the
    filters decide what shows and nothing outside the viewer's scope is ever shown.
    """
    raw = request.GET.get("employee", "").strip()
    if not raw.isdigit():
        return None
    return visible_employees(request.user).filter(pk=raw).first()


def _back_to_monitoring(request, employee):
    """The link to this employee's monitoring page, keeping the dates the list was opened with."""
    dates = urlencode({key: request.GET[key] for key in ("start", "end") if request.GET.get(key)})
    return reverse("dashboard_employee", args=[employee.pk]) + (f"?{dates}" if dates else "")


@role_required(Role.ADMIN, Role.MANAGER)
def screenshot_list(request):
    viewer = request.user
    context_employee = _context_employee(request)
    back_url = _back_to_monitoring(request, context_employee) if context_employee else ""
    if context_employee:
        # An employee's own page: only the dates and the sort order come from the URL. The employee is
        # fixed here, so no other filter can widen the list.
        params = QueryDict(mutable=True)
        for key in ("start", "end", "order"):
            if request.GET.get(key):
                params[key] = request.GET[key]
        params["employee"] = str(context_employee.pk)
    else:
        params = request.GET
    list_url = reverse("screenshot_list") + (f"?employee={context_employee.pk}" if context_employee else "")
    form = ScreenshotFilterForm(viewer, params or None)
    record = EmployeeProfile.objects.filter(user=OuterRef("user_id"), organization=viewer.organization)
    shots = services.visible_screenshots(viewer).annotate(
        employee_code=Subquery(record.values("employee_code")[:1]),
        department_id=Subquery(record.values("department_id")[:1]),
    )
    if form.is_bound and form.is_valid():
        data = form.cleaned_data
        if data["q"]:
            text = data["q"]
            shots = shots.filter(
                Q(user__full_name__icontains=text) | Q(user__email__icontains=text) | Q(employee_code__icontains=text)
            )
        if data["employee"]:
            shots = shots.filter(user=data["employee"])
        if data["team"]:
            members = UserTeam.objects.filter(organization=viewer.organization, team=data["team"])
            shots = shots.filter(user__in=members.values("user_id"))
        if data["department"]:
            shots = shots.filter(department_id=data["department"].pk)
        if data["status"]:
            shots = shots.filter(status=data["status"])
        if data["start"]:
            start, end = services.day_range(viewer.organization, data["start"], data["end"])
            shots = shots.filter(captured_at__gte=start, captured_at__lt=end)
    if context_employee:
        shots = shots.filter(user=context_employee)  # enforced in the query, not only in the form
    oldest_first = params.get("order") == "oldest"
    shots = shots.order_by("captured_at", "id") if oldest_first else shots.order_by("-captured_at", "-id")
    page = Paginator(shots, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "screenshots/list.html",
        {
            "form": form,
            "page": page,
            "statuses": ScreenshotStatus,
            "query": _query_without(params),
            "list_url": list_url,
            "employee_scope": context_employee,
            "can_download": services.can_download(viewer),
            "shown_from": page.start_index() if page.paginator.count else 0,
            "shown_to": page.end_index() if page.paginator.count else 0,
            "context_employee": context_employee,
            "nav_current": "dashboard_employee" if context_employee else "",
            "back_url": back_url,
            **zone_context(viewer.organization),
        },
    )


def _query_without(params):
    query = params.copy()
    query.pop("page", None)
    return query.urlencode()


@role_required(Role.ADMIN, Role.MANAGER)
def screenshot_detail(request, pk):
    shot = services.get_visible_screenshot(request.user, pk)
    if shot is None:
        raise Http404
    return render(
        request,
        "screenshots/detail.html",
        {
            "shot": shot,
            "can_download": services.can_download(request.user),
            "can_delete": services.can_delete(request.user),
            "statuses": ScreenshotStatus,
            **zone_context(request.user.organization),
        },
    )


@role_required(Role.ADMIN)
@require_POST
def screenshot_delete(request, pk):
    shot = services.get_visible_screenshot(request.user, pk)
    if shot is None:
        raise Http404
    try:
        services.delete_screenshot(request.user, shot)
    except services.NotAllowed:
        raise Http404
    messages.success(request, "Screenshot deleted. The deletion is recorded in the audit log.")
    return redirect("screenshot_list")


def _redirect_to(link):
    """Send the browser to a signed link. It is not cached, and it is never shown in a page."""
    response = HttpResponseRedirect(link)
    response["Referrer-Policy"] = "no-referrer"
    return response


@role_required(Role.ADMIN, Role.MANAGER)
@never_cache
def screenshot_image(request, pk):
    shot = services.get_visible_screenshot(request.user, pk)
    if shot is None:
        raise Http404
    try:
        link = services.view_link(request.user, shot)
    except services.NotAllowed:
        raise Http404
    return _redirect_to(link)


@role_required(Role.ADMIN, Role.MANAGER)
@never_cache
def screenshot_download(request, pk):
    shot = services.get_visible_screenshot(request.user, pk)
    if shot is None:
        raise Http404
    try:
        link = services.download_link(request.user, shot)
    except services.NotAllowed:
        raise Http404
    return _redirect_to(link)
