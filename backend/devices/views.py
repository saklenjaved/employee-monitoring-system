"""Admin pages for registration codes and device revocation."""
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.db.models import F
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from access_control.permissions import role_required
from accounts.pin import set_pin
from accounts.ratelimit import client_ip, hit, too_many
from accounts.models import Role
from audit.services import record_event
from workforce.models import UserTeam
from workforce.services import get_person, visible_employees

from .forms import MonitoringPinForm, PairingApproveForm
from .pairing import (
    PairingError,
    approve_request,
    awaiting_first_contact,
    ensure_setup_window,
    issue_enrollment_token,
    issue_install_token,
    open_setup_window,
    open_window_for,
    waiting_pcs_for,
)
from .models import Device, SetupWindow
from .start import give_consent, monitoring_statuses, request_approval
from .services import issue_registration_code, revoke_device, send_setup_invitation

# Guesses per network address per hour, on top of the per-employee lock in accounts.pin.
MONITORING_START_PER_IP_PER_HOUR = 30

PAIRING_MESSAGES = {
    "no_window": "The approval window is closed. Your Admin needs to start setup again.",
    "not_waiting": "That PC is no longer waiting. Check the list again.",
    "no_membership": "Your account is not active in this organization.",
}


@role_required(Role.ADMIN)
@require_POST
@never_cache
def registration_code_create(request, user_id):
    """Create a registration code for an Employee and show it once."""
    person = get_person(request.user, user_id)
    if person is None:
        raise Http404
    if person.role != Role.EMPLOYEE or not person.is_active:
        messages.error(request, "Registration codes can only be created for active Employees.")
        return redirect("person_detail", pk=person.pk)
    code, record = issue_registration_code(request.user, person)
    return render(request, "devices/registration_code.html", {"person": person, "code": code, "record": record})


@role_required(Role.ADMIN)
@require_POST
def monitoring_setup_start(request, user_id):
    """Admin: email the employee a sign-in link to set up monitoring. The email holds no code."""
    person = get_person(request.user, user_id)
    if person is None:
        raise Http404
    if person.role != Role.EMPLOYEE or not person.is_active:
        messages.error(request, "Monitoring setup can only be started for active Employees.")
        return _back(request, person)
    # The window lets this employee approve a waiting PC on their dashboard. Only one may be open per organization.
    _, error = open_setup_window(request.user, person)
    if error == "another_open":
        messages.error(request, "Another setup is still open in your organization. It closes after its time or when that employee approves a PC.")
        return _back(request, person)
    sent = send_setup_invitation(request, person)
    record_event(
        request.user,
        "monitoring_setup.started",
        organization=request.user.organization,
        target=person,
        detail="email sent" if sent else "email failed",
    )
    if sent:
        messages.success(request, f"{person.full_name} was emailed a sign-in link to set up monitoring.")
    else:
        messages.error(request, "The email could not be sent. The server terminal shows the reason.")
    return _back(request, person)


@role_required(Role.ADMIN)
@require_POST
def device_revoke(request, pk):
    device = Device.objects.filter(pk=pk, organization=request.user.organization).first()
    if device is None:
        raise Http404
    revoke_device(request.user, device)
    messages.success(request, f"Device '{device.name}' was revoked.")
    return redirect("person_detail", pk=device.user_id)


@role_required(Role.EMPLOYEE)
@require_POST
@never_cache
def monitoring_pin_set(request):
    """Employee: set or change their monitoring PIN. The current password is required."""
    form = MonitoringPinForm(request.POST, user=request.user)
    if form.is_valid():
        set_pin(request.user, form.cleaned_data["pin"])
        messages.success(request, "Your monitoring PIN is saved.")
    else:
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    return redirect("dashboard_me")


@role_required(Role.EMPLOYEE)
@require_POST
@never_cache
def monitoring_start(request):
    """Employee: ask the agent on their already-approved PC to start monitoring. No PIN is needed."""
    ip_key = f"monitoring-start-ip:{client_ip(request)}"
    if too_many(ip_key, MONITORING_START_PER_IP_PER_HOUR):
        messages.error(request, "Too many attempts from this network. Try again later.")
        return redirect("dashboard_me")
    hit(ip_key, 60 * 60)

    _, reason = give_consent(request.user)
    if reason == "no_pc":
        messages.error(request, "No computer is connected to your account yet.")
    elif reason == "not_approved":
        messages.error(request, "Your administrator has not approved monitoring yet.")
    elif reason == "not_registered":
        messages.error(request, "Your PC has not connected yet. Start WinAgent on your PC, then try again.")
    else:
        messages.success(request, "Monitoring is starting on your PC. It shows Monitoring Active once the agent confirms.")
    return redirect("dashboard_me")


DEPLOY_SCRIPT = """# Silent install of WinAgent for one signed-in user. No admin rights needed. Run it as the signed-in user.
# Intune: run as "User". RMM or logon script: run in the user's session.
param(
    [Parameter(Mandatory = $true)][string]$OrgToken,
    [string]$Installer = "$PSScriptRoot\\{installer}",
    [string]$CaFile = "",
    [switch]$Screenshots   # also turn on screenshots on this PC. The organization's screenshot policy must be on too.
)
$installArgs = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/APIURL={api_url}', "/ORGTOKEN=$OrgToken")
if ($CaFile) {{ $installArgs += "/CAFILE=$CaFile" }}
if ($Screenshots) {{ $installArgs += '/SCREENSHOTS=1' }}
$process = Start-Process -FilePath $Installer -ArgumentList $installArgs -PassThru
$process | Wait-Process -Timeout 180 -ErrorAction SilentlyContinue
if ($process.HasExited) {{ exit $process.ExitCode }} else {{ exit 1 }}
"""


def _agent_api_url(request):
    """The https address the agent connects to, or None. AGENT_BASE_URL wins, so the dashboard itself may be on http in development."""
    configured = settings.AGENT_BASE_URL
    if configured:
        return configured if configured.startswith("https://") else None
    return request.build_absolute_uri("/api/agent/v1") if request.is_secure() else None


INSTALL_FILTERS = (
    ("", "All installs"),
    ("waiting", "Waiting for installation"),
    ("completed", "Installation completed"),
)
CONNECTION_FILTERS = (
    ("", "All connection states"),
    ("pairing_pending", "Waiting for employee approval"),
    ("connecting", "Connecting"),
    ("connected", "Device connected"),
    ("offline", "Device offline"),
    ("failed", "Connection failed"),
)
# Connection stages a PC goes through, as (label, what the Admin should know).
STAGE_LABELS = {
    "waiting_install": ("Waiting for installation", "The employee has not installed WinAgent yet."),
    "pairing_pending": ("Waiting for employee approval", "WinAgent asked to connect. The employee approves the PC on their dashboard."),
    "connecting": ("Connecting", "Approved. WinAgent is connecting to the server."),
    "connected": ("Device connected", "The PC is online and checking in."),
    "offline": ("Device offline", "The PC has not checked in recently."),
    "failed": ("Connection failed", "WinAgent reported an error on this PC."),
}
# Monitoring states from devices.start, as (label, next step for the Admin).
MONITORING_LABELS = {
    "not_connected": ("Not started", ""),
    "ready_to_start": ("Not started", "Click Start monitoring. The employee then approves it on their dashboard."),
    "awaiting_consent": ("Waiting for employee approval", "Waiting for the employee to approve monitoring on their dashboard."),
    "start_requested": ("Start sent, PC offline", "The start is delivered when the PC is back online."),
    "starting": ("Start sent", "The agent picks it up at its next check-in, about a minute."),
    "monitoring_active": ("Monitoring active", "Monitoring is active."),
    "monitoring_paused": ("Monitoring paused", "Paused by the agent (outside monitoring hours or a remote session). It resumes by itself."),
    "device_offline": ("Device offline", "Waiting for the PC to come online."),
    "error": ("Error", "Monitoring could not start. Check the PC or contact IT."),
}


# Pill colors shared with the rest of the app: ok (green), wait (amber), bad (red), or muted (no color).
STAGE_TONE = {
    "waiting_install": "",
    "pairing_pending": "wait",
    "connecting": "wait",
    "connected": "ok",
    "offline": "",
    "failed": "bad",
}
MONITORING_TONE = {
    "not_connected": "",
    "ready_to_start": "",
    "awaiting_consent": "wait",
    "start_requested": "wait",
    "starting": "wait",
    "monitoring_active": "ok",
    "monitoring_paused": "wait",
    "device_offline": "",
    "error": "bad",
}


def _back(request, person):
    """Return to the deployment page when the action was started there, otherwise to the employee's page."""
    if request.POST.get("next") == "deployment":
        return redirect("deployment")
    return redirect("person_detail", pk=person.pk)


def _deployment_row(person, device, statuses, link_open, teams, now):
    """One employee's deployment state, from the real device, pairing, setup window and monitoring records."""
    row = {
        "pk": person.pk,
        "name": person.full_name,
        "email": person.email,
        "teams": ", ".join(teams) or "No team",
        "device_name": "",
        "last_seen": None,
    }
    if device is None:
        waiting_approval = person.pk in link_open and waiting_pcs_for(person, now).exists()
        stage = "pairing_pending" if waiting_approval else "waiting_install"
        monitor_key = "not_connected"
    else:
        row["device_name"] = device.name
        row["last_seen"] = device.last_seen_at
        monitor_key = statuses[device.pk]
        if device.last_error_code:
            stage = "failed"
        elif device.is_online:
            stage = "connected"
        elif awaiting_first_contact(person, now) is not None:
            stage = "connecting"
        else:
            stage = "offline"
    row["stage"] = stage
    row["stage_label"], row["stage_help"] = STAGE_LABELS[stage]
    row["install"] = "waiting" if stage == "waiting_install" else "completed"
    row["install_label"] = "Not installed" if row["install"] == "waiting" else "Installed"
    row["monitor_key"] = monitor_key
    row["monitor_label"] = MONITORING_LABELS[monitor_key][0]
    if device is None:
        row["next_text"] = (
            "Setup link sent. Waiting for the employee to install WinAgent."
            if person.pk in link_open
            else "Send the setup link. The employee then downloads WinAgent from their dashboard."
        )
    elif stage == "connected":
        row["next_text"] = MONITORING_LABELS[monitor_key][1]
    elif stage == "pairing_pending":
        row["next_text"] = "Waiting for the employee to approve this PC on their dashboard."
    elif stage == "connecting":
        row["next_text"] = "Wait a minute. WinAgent is connecting to the server."
    elif stage == "offline":
        row["next_text"] = "Waiting for the PC to come online."
    else:
        row["next_text"] = "Ask the employee to reinstall WinAgent, or check the PC with IT."
    row["send_link"] = device is None and person.pk not in link_open
    row["resend_link"] = device is None and person.pk in link_open
    row["can_start"] = stage == "connected" and monitor_key == "ready_to_start"
    row["monitoring_active"] = monitor_key == "monitoring_active"
    row["stage_tone"] = STAGE_TONE[stage]
    row["monitor_tone"] = MONITORING_TONE[monitor_key]
    return row


def _deployment_summary(rows):
    """Card numbers, all from the rows (not from the filtered list)."""
    return {
        "employees": len(rows),
        "installed": sum(r["install"] == "completed" for r in rows),
        "connected": sum(r["stage"] == "connected" for r in rows),
        "pending_setup": sum(
            r["install"] == "completed" and r["monitor_key"] not in ("monitoring_active", "monitoring_paused") for r in rows
        ),
        "monitoring_active": sum(r["monitoring_active"] for r in rows),
        "failed": sum(r["stage"] == "failed" for r in rows),
        "needs_link": sum(r["send_link"] for r in rows),
        "waiting_approval": sum(r["stage"] == "pairing_pending" for r in rows),
        "ready": sum(r["can_start"] for r in rows),
        "offline": sum(r["stage"] == "offline" for r in rows),
    }


def _deployment_rows(admin, query="", install="", connection="", now=None):
    """Rows for the table (after search and filters), the summary cards and the four steps."""
    now = now or timezone.now()
    employees = list(visible_employees(admin).filter(is_active=True).order_by("full_name"))
    latest = {}
    devices = Device.objects.filter(
        organization=admin.organization, user__in=employees, is_active=True
    ).order_by(F("last_seen_at").desc(nulls_last=True), "-registered_at")
    for device in devices:
        latest.setdefault(device.user_id, device)
    statuses = monitoring_statuses(latest.values(), now)
    link_open = set(
        SetupWindow.objects.filter(
            organization=admin.organization, user__in=employees, closed_at__isnull=True, expires_at__gt=now
        ).values_list("user_id", flat=True)
    )
    team_names = defaultdict(list)
    for user_id, team_name in UserTeam.objects.filter(organization=admin.organization, user__in=employees).values_list(
        "user_id", "team__name"
    ):
        team_names[user_id].append(team_name)

    rows = [
        _deployment_row(person, latest.get(person.pk), statuses, link_open, sorted(team_names[person.pk]), now)
        for person in employees
    ]
    summary = _deployment_summary(rows)
    needle = query.strip().lower()
    shown = [
        r for r in rows
        if (not needle or needle in r["name"].lower() or needle in r["email"].lower())
        and (not install or r["install"] == install)
        and (not connection or r["stage"] == connection)
    ]
    return shown, summary


def _setup_steps(summary):
    """The four setup steps, each done only when every active employee has reached it. Live from the records."""
    total = summary["employees"]
    done = [
        (f"Employee account is active ({total})", total > 0),
        (f"WinAgent installed ({summary['installed']} of {total})", total > 0 and summary["installed"] == total),
        (f"PC connected and approved ({summary['connected']} of {total})", total > 0 and summary["connected"] == total),
        (f"Monitoring active ({summary['monitoring_active']} of {total})", total > 0 and summary["monitoring_active"] == total),
    ]
    current = next((i for i, (_, finished) in enumerate(done) if not finished), None)
    return [
        {"label": label, "done": finished, "current": index == current}
        for index, (label, finished) in enumerate(done)
    ]


def _deployment_context(request, token=None):
    installer = Path(settings.AGENT_INSTALLER_PATH)
    query = request.GET.get("q", "")
    install = request.GET.get("install", "")
    connection = request.GET.get("connection", "")
    rows, summary = _deployment_rows(request.user, query, install, connection)
    return {
        "api_url": _agent_api_url(request) or request.build_absolute_uri("/api/agent/v1"),
        "installer_name": installer.name,
        "installer_ready": installer.is_file(),
        "token": token,
        "has_token": bool(request.user.organization.enrollment_token_hash),
        "rows": rows,
        "summary": summary,
        "setup_steps": _setup_steps(summary),
        "query": query,
        "install": install,
        "connection": connection,
        "install_filters": INSTALL_FILTERS,
        "connection_filters": CONNECTION_FILTERS,
        "filtered": bool(query or install or connection),
    }


@role_required(Role.ADMIN)
@never_cache
def deployment(request):
    """Admin: how to push the agent to every PC with an IT tool. The token is shown only when just made."""
    return render(request, "devices/deployment.html", _deployment_context(request))


@role_required(Role.ADMIN)
@require_POST
@never_cache
def deployment_token_new(request):
    """Admin: make a new organization installer token. It replaces the old one and is shown once."""
    token = issue_enrollment_token(request.user.organization)
    record_event(request.user, "enrollment_token.issued", organization=request.user.organization, target=request.user.organization)
    return render(request, "devices/deployment.html", _deployment_context(request, token=token))


@role_required(Role.ADMIN)
def deployment_installer(request):
    """Admin: download the built installer, for IT to push. It holds no secret."""
    installer = Path(settings.AGENT_INSTALLER_PATH)
    if not installer.is_file():
        raise Http404
    return FileResponse(installer.open("rb"), as_attachment=True, filename=installer.name)


@role_required(Role.ADMIN)
def deployment_script(request):
    """Admin: download the PowerShell script IT runs on each PC. It holds no secret; the token is a parameter."""
    api_url = _agent_api_url(request) or request.build_absolute_uri("/api/agent/v1")
    script = DEPLOY_SCRIPT.format(installer=Path(settings.AGENT_INSTALLER_PATH).name, api_url=api_url)
    response = HttpResponse(script, content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="Deploy-WinAgent.ps1"'
    return response


@role_required(Role.EMPLOYEE)
@require_POST
@never_cache
def install_package(request):
    """Employee: download the WinAgent installer for this employee's own PC. The employee must tick the consent box.

    Each download opens a short approval window (for the one password step) and gets a new token.
    The token is never logged or audited.
    """
    window = ensure_setup_window(request.user)
    if window is None:
        messages.error(request, "Another employee is connecting a PC right now. Try again in a few minutes.")
        return redirect("dashboard_me")
    # The employee must approve the installation first. Nothing is issued without it.
    if request.POST.get("consent") != "yes":
        messages.error(request, "Tick the box to approve installing WinAgent on this PC.")
        return redirect("dashboard_me")
    installer = Path(settings.AGENT_INSTALLER_PATH)
    if not installer.is_file():
        messages.error(request, "The installer is not available on this server. Ask your Admin.")
        return redirect("dashboard_me")
    # The agent always connects over https. The dashboard may be on http only in development.
    if not request.is_secure() and not settings.DEV_MODE:
        messages.error(request, "Open this site over https and try again. The setup file holds a one-time token.")
        return redirect("dashboard_me")
    api_url = _agent_api_url(request)
    if api_url is None:
        messages.error(request, "The server's https address for agents is not set. Ask your Admin (AGENT_BASE_URL).")
        return redirect("dashboard_me")
    token = issue_install_token(window)
    record_event(request.user, "setup.install_package_downloaded", organization=request.user.organization, target=window)
    # One file, no zip or script. The token is in the file name, and the installer reads it from there.
    return FileResponse(installer.open("rb"), as_attachment=True, filename=f"WinAgentSetup-{token}.exe")


@role_required(Role.ADMIN)
@require_POST
def admin_monitoring_start(request, user_id):
    """Admin: start monitoring on an employee's PC that the employee already approved.

    The agent on that PC is running and checks in about once a minute, so it picks the start up then.
    """
    person = get_person(request.user, user_id)
    if person is None:
        raise Http404
    if person.role != Role.EMPLOYEE or not person.is_active:
        messages.error(request, "Monitoring can only be started for active Employees.")
        return _back(request, person)
    _, reason = request_approval(person, actor=request.user)
    if reason == "no_pc":
        messages.error(request, f"{person.full_name} has no connected PC yet. Start monitoring setup first.")
    else:
        messages.success(request, f"Approval sent to {person.full_name}. Monitoring starts after they confirm on their dashboard.")
    return _back(request, person)


@role_required(Role.EMPLOYEE)
@require_POST
@never_cache
def pairing_approve(request, request_id):
    """Employee: approve one waiting PC after re-entering the account password."""
    form = PairingApproveForm(request.POST, user=request.user)
    if not form.is_valid():
        messages.error(request, form.errors["current_password"][0])
        return redirect("dashboard_me")
    try:
        device = approve_request(request.user, request_id)
    except PairingError as error:
        messages.error(request, PAIRING_MESSAGES[error.code])
    else:
        messages.success(request, f"{device.name} is connected. Monitoring starts on it in the background.")
    return redirect("dashboard_me")
