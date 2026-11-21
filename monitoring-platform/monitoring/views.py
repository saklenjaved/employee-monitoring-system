from django.contrib import messages
from django.shortcuts import redirect, render

from access_control.permissions import role_required
from accounts.models import Role

from .forms import MonitoringPolicyForm
from .services import get_policy, update_policy


@role_required(Role.ADMIN)
def policy_settings(request):
    """The Admin edits the monitoring policy of their own organization."""
    organization = request.user.organization
    policy = get_policy(organization)
    initial = {
        "enabled": policy.enabled,
        "days": policy.days,
        "start_time": policy.start_time,
        "end_time": policy.end_time,
        "sample_interval_seconds": policy.sample_interval_seconds,
        "idle_threshold_seconds": policy.idle_threshold_seconds,
        "sync_interval_seconds": policy.sync_interval_seconds,
        "timezone": policy.timezone,
    }
    form = MonitoringPolicyForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        update_policy(request.user, organization, form.cleaned_data)
        messages.success(request, "Monitoring settings saved. Agents pick them up on their next policy refresh.")
        return redirect("policy_settings")
    return render(request, "monitoring/settings.html", {"form": form, "policy": policy})
